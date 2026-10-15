-- imd_agromet schema (safe to run again)

-- IMD -> LGD mapping (masters/*.csv)
CREATE TABLE IF NOT EXISTS imd_state_map (
  imd_state_id   integer PRIMARY KEY,
  state_name     text,
  state_reg_name text,
  latitude       double precision,
  longitude      double precision,
  lgd_state_code integer
);

CREATE TABLE IF NOT EXISTS imd_district_map (
  imd_district_id   integer PRIMARY KEY,
  imd_state_id      integer,
  district_name     text,
  lgd_district_code integer,
  lgd_state_code    integer,
  source            text      -- census2011+name | census2011_renamed | name | manual | unmatched
);

CREATE TABLE IF NOT EXISTS imd_crop_map (
  type_id integer PRIMARY KEY,
  crop    text,
  source  text
);

-- Location Resolver keys, once per LGD district (loaded from masters/district_keys.csv.gz)
CREATE TABLE IF NOT EXISTS district_keys (
  lgd_district_code integer NOT NULL,
  district_name     text,
  key               text COLLATE "C" NOT NULL,
  key_type          text,                     -- inside | edge
  PRIMARY KEY (lgd_district_code, key)
);
CREATE INDEX IF NOT EXISTS district_keys_key_idx ON district_keys (key);

-- IMD bulletin rows exactly as received, plus the LGD district they map to
CREATE TABLE IF NOT EXISTS advisories (
  id                text PRIMARY KEY,
  cat_id            text,
  state_id          text,
  district_id       text,
  custom_date       text,                     -- YYYY-MM-DD, bulletin date
  type_id           text,
  variety_id        text,
  advisory_eng      text,
  advisory_reg      text,
  crop_stage        text,
  crop_state        text,
  raw_json          jsonb NOT NULL,
  lgd_district_code integer,
  valid_until       date,                     -- last day (IST) the bulletin may be shown
  fetched_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz
);
ALTER TABLE advisories ADD COLUMN IF NOT EXISTS valid_until date;
CREATE INDEX IF NOT EXISTS advisories_lgd_date_idx ON advisories (lgd_district_code, custom_date);
CREATE INDEX IF NOT EXISTS advisories_date_idx ON advisories (custom_date);

CREATE TABLE IF NOT EXISTS fetches (
  id           serial PRIMARY KEY,
  bulletin     text,
  source       text,
  rows         integer,
  new_rows     integer,
  changed_rows integer,
  fetched_at   timestamptz NOT NULL DEFAULT now()
);

-- IMD issues on Tuesday and Friday. A bulletin is valid until 11:59 PM IST on the day before the next
-- issue day (Tuesday -> Thursday, Friday -> Monday), even if the next bulletin is late.
CREATE OR REPLACE FUNCTION imd_valid_until(issued date)
RETURNS date
LANGUAGE sql IMMUTABLE AS $$
  SELECT min(day)::date - 1
  FROM generate_series(issued + 1, issued + 7, interval '1 day') AS day
  WHERE extract(isodow FROM day) IN (2, 5)
$$;

-- IMD text comes with tabs, \r\n and a private-use bullet character; keep one bullet item per line.
CREATE OR REPLACE FUNCTION imd_clean_text(t text)
RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT btrim(regexp_replace(regexp_replace(regexp_replace(
           replace(replace(t, E'\r', ''), U&'\F0D8', '•'),
           E'[\t ]+', ' ', 'g'),
           E' ?\n ?', E'\n', 'g'),
           E'\n{2,}', E'\n', 'g'), E' \n')
$$;

-- Districts whose keys overlap the given keys, with their latest bulletin that is still valid today (IST).
CREATE OR REPLACE FUNCTION imd_lookup(p_keys text[], p_crop text DEFAULT NULL, p_date text DEFAULT NULL)
RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
BEGIN
RETURN (
WITH fk AS (
  SELECT DISTINCT k::text COLLATE "C" AS k FROM unnest(p_keys) AS k WHERE k <> ''
),
hits AS (
  -- district key inside the farmer key (range scan on the key index)
  SELECT dk.lgd_district_code, dk.district_name
  FROM fk JOIN district_keys dk ON dk.key >= fk.k AND dk.key < fk.k || '4'
  UNION ALL
  -- farmer key inside the district key (the district key is a prefix of it)
  SELECT dk.lgd_district_code, dk.district_name
  FROM fk CROSS JOIN LATERAL generate_series(1, length(fk.k)) AS i
  JOIN district_keys dk ON dk.key = left(fk.k, i)
),
d AS (
  SELECT lgd_district_code, min(district_name) AS district_name FROM hits GROUP BY lgd_district_code
),
dt AS (
  SELECT COALESCE(p_date, (SELECT max(a.custom_date) FROM advisories a
                            WHERE a.lgd_district_code IN (SELECT lgd_district_code FROM d)
                              AND a.valid_until >= (now() AT TIME ZONE 'Asia/Kolkata')::date)) AS v
),
adv AS (
  SELECT a.id, a.custom_date, a.lgd_district_code, d.district_name, a.cat_id, a.type_id, c.crop,
         a.crop_stage, imd_clean_text(a.advisory_eng) AS advisory_eng, imd_clean_text(a.advisory_reg) AS advisory_reg
  FROM advisories a
  JOIN d ON d.lgd_district_code = a.lgd_district_code
  LEFT JOIN imd_crop_map c ON c.type_id::text = a.type_id
  WHERE a.custom_date = (SELECT v FROM dt)
    AND (p_crop IS NULL OR lower(c.crop) = lower(p_crop))
)
SELECT jsonb_build_object(
  'districts', COALESCE((SELECT jsonb_agg(jsonb_build_object('lgd_district_code', lgd_district_code,
                                                             'name', district_name) ORDER BY lgd_district_code)
                         FROM d), '[]'::jsonb),
  'date', (SELECT v FROM dt),
  'valid_until', (SELECT imd_valid_until(v::date) FROM dt),
  'advisories', COALESCE((SELECT jsonb_agg(to_jsonb(adv) ORDER BY lgd_district_code, length(id), id)
                          FROM adv), '[]'::jsonb)
)
);
END;
$$;
