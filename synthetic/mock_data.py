"""
Data pools for synthetic conversation generation.
Indian names, locations, mandi names, crop price ranges, weather ranges, soil health ranges.
"""

import json
import os
import random
import string
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from faker import Faker

load_dotenv()

fake = Faker("en_IN")

# ─── Photon Geocoder Config ──────────────────────────────────────────────────

_photon_url = os.getenv("PHOTON_HOST", "http://10.128.188.15:2322")
_parsed = urlparse(_photon_url)
_PHOTON_HOST = _parsed.hostname or "10.128.188.15"
_PHOTON_PORT = _parsed.port or 2322
_PHOTON_API = f"http://{_PHOTON_HOST}:{_PHOTON_PORT}/api"

_INDIA_BBOX = "68.0,6.0,98.0,36.0"

_QUERY_SEEDS = list(string.ascii_lowercase) + [
    "pur", "garh", "nagar", "bad", "ganj", "pura", "wadi", "palli",
    "khera", "patti", "kalan", "khurd", "basti", "tola", "gaon",
]

# ─── Failure Simulation ──────────────────────────────────────────────────────────

MOCK_FAILURE_RATE = 0.05  # 5% chance of simulated service failure


def should_fail() -> bool:
    """Return True if this call should simulate a failure."""
    return random.random() < MOCK_FAILURE_RATE


# ─── Indian Names (via Faker en_IN) ─────────────────────────────────────────────


def random_name() -> str:
    return fake.name()


# ─── Indian Locations (state, district, village, lat, lon) ──────────────────────

LOCATIONS: list[dict] = [
    # Madhya Pradesh
    {"state": "Madhya Pradesh", "district": "Indore", "village": "Mhow"},
    {"state": "Madhya Pradesh", "district": "Bhopal", "village": "Berasia"},
    {"state": "Madhya Pradesh", "district": "Sagar", "village": "Khurai"},
    {"state": "Madhya Pradesh", "district": "Ujjain", "village": "Nagda"},
    {"state": "Madhya Pradesh", "district": "Jabalpur", "village": "Sihora"},
    {"state": "Madhya Pradesh", "district": "Gwalior", "village": "Dabra"},
    {"state": "Madhya Pradesh", "district": "Rewa", "village": "Mauganj"},
    {"state": "Madhya Pradesh", "district": "Dewas", "village": "Sonkatch"},
    {"state": "Madhya Pradesh", "district": "Hoshangabad", "village": "Seoni Malwa"},
    {"state": "Madhya Pradesh", "district": "Chhindwara", "village": "Pandhurna"},
    # Uttar Pradesh
    {"state": "Uttar Pradesh", "district": "Lucknow", "village": "Malihabad"},
    {"state": "Uttar Pradesh", "district": "Varanasi", "village": "Ramnagar"},
    {"state": "Uttar Pradesh", "district": "Agra", "village": "Fatehabad"},
    {"state": "Uttar Pradesh", "district": "Kanpur", "village": "Bilhaur"},
    {"state": "Uttar Pradesh", "district": "Allahabad", "village": "Phulpur"},
    {"state": "Uttar Pradesh", "district": "Meerut", "village": "Sardhana"},
    {"state": "Uttar Pradesh", "district": "Gorakhpur", "village": "Campierganj"},
    {"state": "Uttar Pradesh", "district": "Bareilly", "village": "Faridpur"},
    {"state": "Uttar Pradesh", "district": "Moradabad", "village": "Thakurdwara"},
    {"state": "Uttar Pradesh", "district": "Jhansi", "village": "Moth"},
    {"state": "Uttar Pradesh", "district": "Sultanpur", "village": "Kadipur"},
    {"state": "Uttar Pradesh", "district": "Pratapgarh", "village": "Kunda"},
    {"state": "Uttar Pradesh", "district": "Azamgarh", "village": "Phulpur"},
    {"state": "Uttar Pradesh", "district": "Mathura", "village": "Chhata"},
    # Rajasthan
    {"state": "Rajasthan", "district": "Jaipur", "village": "Chomu"},
    {"state": "Rajasthan", "district": "Jodhpur", "village": "Osian"},
    {"state": "Rajasthan", "district": "Kota", "village": "Sangod"},
    {"state": "Rajasthan", "district": "Udaipur", "village": "Salumber"},
    {"state": "Rajasthan", "district": "Ajmer", "village": "Kishangarh"},
    {"state": "Rajasthan", "district": "Bikaner", "village": "Nokha"},
    {"state": "Rajasthan", "district": "Alwar", "village": "Behror"},
    {"state": "Rajasthan", "district": "Sikar", "village": "Lachhmangarh"},
    {"state": "Rajasthan", "district": "Nagaur", "village": "Degana"},
    {"state": "Rajasthan", "district": "Bhilwara", "village": "Mandal"},
    # Maharashtra
    {"state": "Maharashtra", "district": "Pune", "village": "Junnar"},
    {"state": "Maharashtra", "district": "Nagpur", "village": "Kamptee"},
    {"state": "Maharashtra", "district": "Nashik", "village": "Sinnar"},
    {"state": "Maharashtra", "district": "Latur", "village": "Udgir"},
    {"state": "Maharashtra", "district": "Aurangabad", "village": "Paithan"},
    {"state": "Maharashtra", "district": "Solapur", "village": "Pandharpur"},
    {"state": "Maharashtra", "district": "Kolhapur", "village": "Hatkanangle"},
    {"state": "Maharashtra", "district": "Satara", "village": "Wai"},
    {"state": "Maharashtra", "district": "Sangli", "village": "Miraj"},
    {"state": "Maharashtra", "district": "Ahmednagar", "village": "Shrirampur"},
    {"state": "Maharashtra", "district": "Jalgaon", "village": "Chopda"},
    {"state": "Maharashtra", "district": "Beed", "village": "Georai"},
    {"state": "Maharashtra", "district": "Osmanabad", "village": "Tuljapur"},
    {"state": "Maharashtra", "district": "Yavatmal", "village": "Pusad"},
    # Gujarat
    {"state": "Gujarat", "district": "Ahmedabad", "village": "Dholka"},
    {"state": "Gujarat", "district": "Rajkot", "village": "Gondal"},
    {"state": "Gujarat", "district": "Surat", "village": "Bardoli"},
    {"state": "Gujarat", "district": "Vadodara", "village": "Padra"},
    {"state": "Gujarat", "district": "Junagadh", "village": "Visavadar"},
    {"state": "Gujarat", "district": "Bhavnagar", "village": "Palitana"},
    {"state": "Gujarat", "district": "Mehsana", "village": "Visnagar"},
    {"state": "Gujarat", "district": "Banaskantha", "village": "Deesa"},
    {"state": "Gujarat", "district": "Kutch", "village": "Mundra"},
    # Punjab
    {"state": "Punjab", "district": "Ludhiana", "village": "Jagraon"},
    {"state": "Punjab", "district": "Amritsar", "village": "Ajnala"},
    {"state": "Punjab", "district": "Patiala", "village": "Rajpura"},
    {"state": "Punjab", "district": "Bathinda", "village": "Rampura Phul"},
    {"state": "Punjab", "district": "Sangrur", "village": "Malerkotla"},
    {"state": "Punjab", "district": "Jalandhar", "village": "Nakodar"},
    {"state": "Punjab", "district": "Ferozepur", "village": "Zira"},
    {"state": "Punjab", "district": "Moga", "village": "Baghapurana"},
    {"state": "Punjab", "district": "Mansa", "village": "Budhlada"},
    # Haryana
    {"state": "Haryana", "district": "Karnal", "village": "Gharaunda"},
    {"state": "Haryana", "district": "Hisar", "village": "Hansi"},
    {"state": "Haryana", "district": "Sirsa", "village": "Dabwali"},
    {"state": "Haryana", "district": "Rohtak", "village": "Meham"},
    {"state": "Haryana", "district": "Sonipat", "village": "Gohana"},
    {"state": "Haryana", "district": "Ambala", "village": "Barara"},
    {"state": "Haryana", "district": "Kurukshetra", "village": "Pehowa"},
    {"state": "Haryana", "district": "Jhajjar", "village": "Bahadurgarh"},
    {"state": "Haryana", "district": "Fatehabad", "village": "Ratia"},
    {"state": "Haryana", "district": "Jind", "village": "Narwana"},
    # Bihar
    {"state": "Bihar", "district": "Patna", "village": "Danapur"},
    {"state": "Bihar", "district": "Muzaffarpur", "village": "Sahebganj"},
    {"state": "Bihar", "district": "Bhagalpur", "village": "Kahalgaon"},
    {"state": "Bihar", "district": "Gaya", "village": "Tekari"},
    {"state": "Bihar", "district": "Darbhanga", "village": "Benipur"},
    {"state": "Bihar", "district": "Purnia", "village": "Baisi"},
    {"state": "Bihar", "district": "Vaishali", "village": "Hajipur"},
    {"state": "Bihar", "district": "Samastipur", "village": "Rosera"},
    {"state": "Bihar", "district": "Begusarai", "village": "Teghra"},
    {"state": "Bihar", "district": "Nalanda", "village": "Hilsa"},
    # Karnataka
    {"state": "Karnataka", "district": "Bengaluru Rural", "village": "Devanahalli"},
    {"state": "Karnataka", "district": "Mysuru", "village": "Nanjangud"},
    {"state": "Karnataka", "district": "Belgaum", "village": "Gokak"},
    {"state": "Karnataka", "district": "Dharwad", "village": "Navalgund"},
    {"state": "Karnataka", "district": "Haveri", "village": "Ranebennur"},
    {"state": "Karnataka", "district": "Shimoga", "village": "Bhadravathi"},
    {"state": "Karnataka", "district": "Raichur", "village": "Sindhanur"},
    {"state": "Karnataka", "district": "Bellary", "village": "Hospet"},
    {"state": "Karnataka", "district": "Tumkur", "village": "Tiptur"},
    {"state": "Karnataka", "district": "Hassan", "village": "Channarayapatna"},
    {"state": "Karnataka", "district": "Mandya", "village": "Pandavapura"},
    # Tamil Nadu
    {"state": "Tamil Nadu", "district": "Coimbatore", "village": "Pollachi"},
    {"state": "Tamil Nadu", "district": "Thanjavur", "village": "Kumbakonam"},
    {"state": "Tamil Nadu", "district": "Madurai", "village": "Melur"},
    {"state": "Tamil Nadu", "district": "Salem", "village": "Attur"},
    {"state": "Tamil Nadu", "district": "Erode", "village": "Bhavani"},
    {"state": "Tamil Nadu", "district": "Tirunelveli", "village": "Ambasamudram"},
    {"state": "Tamil Nadu", "district": "Dindigul", "village": "Palani"},
    {"state": "Tamil Nadu", "district": "Villupuram", "village": "Tindivanam"},
    {"state": "Tamil Nadu", "district": "Tiruvannamalai", "village": "Polur"},
    {"state": "Tamil Nadu", "district": "Nagapattinam", "village": "Sirkali"},
    # Andhra Pradesh
    {"state": "Andhra Pradesh", "district": "Guntur", "village": "Tenali"},
    {"state": "Andhra Pradesh", "district": "Krishna", "village": "Machilipatnam"},
    {"state": "Andhra Pradesh", "district": "East Godavari", "village": "Amalapuram"},
    {"state": "Andhra Pradesh", "district": "West Godavari", "village": "Narsapuram"},
    {"state": "Andhra Pradesh", "district": "Kurnool", "village": "Nandyal"},
    {"state": "Andhra Pradesh", "district": "Anantapur", "village": "Dharmavaram"},
    {"state": "Andhra Pradesh", "district": "Chittoor", "village": "Madanapalle"},
    {"state": "Andhra Pradesh", "district": "Prakasam", "village": "Markapur"},
    # Telangana
    {"state": "Telangana", "district": "Warangal", "village": "Jangaon"},
    {"state": "Telangana", "district": "Karimnagar", "village": "Huzurabad"},
    {"state": "Telangana", "district": "Nizamabad", "village": "Bodhan"},
    {"state": "Telangana", "district": "Khammam", "village": "Kothagudem"},
    {"state": "Telangana", "district": "Nalgonda", "village": "Miryalaguda"},
    {"state": "Telangana", "district": "Mahabubnagar", "village": "Jadcherla"},
    {"state": "Telangana", "district": "Medak", "village": "Siddipet"},
    # West Bengal
    {"state": "West Bengal", "district": "Burdwan", "village": "Memari"},
    {"state": "West Bengal", "district": "Hooghly", "village": "Arambagh"},
    {"state": "West Bengal", "district": "Murshidabad", "village": "Berhampore"},
    {"state": "West Bengal", "district": "Nadia", "village": "Ranaghat"},
    {"state": "West Bengal", "district": "Birbhum", "village": "Rampurhat"},
    {"state": "West Bengal", "district": "Bankura", "village": "Bishnupur"},
    {"state": "West Bengal", "district": "Midnapore West", "village": "Kharagpur"},
    # Odisha
    {"state": "Odisha", "district": "Cuttack", "village": "Banki"},
    {"state": "Odisha", "district": "Ganjam", "village": "Berhampur"},
    {"state": "Odisha", "district": "Balasore", "village": "Jaleswar"},
    {"state": "Odisha", "district": "Sambalpur", "village": "Kuchinda"},
    {"state": "Odisha", "district": "Mayurbhanj", "village": "Rairangpur"},
    {"state": "Odisha", "district": "Puri", "village": "Nimapara"},
    # Chhattisgarh
    {"state": "Chhattisgarh", "district": "Raipur", "village": "Abhanpur"},
    {"state": "Chhattisgarh", "district": "Durg", "village": "Patan"},
    {"state": "Chhattisgarh", "district": "Bilaspur", "village": "Takhatpur"},
    {"state": "Chhattisgarh", "district": "Rajnandgaon", "village": "Dongargarh"},
    {"state": "Chhattisgarh", "district": "Korba", "village": "Katghora"},
    # Jharkhand
    {"state": "Jharkhand", "district": "Ranchi", "village": "Kanke"},
    {"state": "Jharkhand", "district": "Dhanbad", "village": "Tundi"},
    {"state": "Jharkhand", "district": "Hazaribagh", "village": "Barhi"},
    {"state": "Jharkhand", "district": "Giridih", "village": "Deori"},
    {"state": "Jharkhand", "district": "Dumka", "village": "Saraiyahat"},
    # Assam
    {"state": "Assam", "district": "Kamrup", "village": "Mirza"},
    {"state": "Assam", "district": "Nagaon", "village": "Hojai"},
    {"state": "Assam", "district": "Sonitpur", "village": "Dhekiajuli"},
    {"state": "Assam", "district": "Dibrugarh", "village": "Naharkatia"},
    {"state": "Assam", "district": "Jorhat", "village": "Titabar"},
    # Kerala
    {"state": "Kerala", "district": "Thrissur", "village": "Chalakudy"},
    {"state": "Kerala", "district": "Palakkad", "village": "Chittur"},
    {"state": "Kerala", "district": "Wayanad", "village": "Sulthan Bathery"},
    {"state": "Kerala", "district": "Idukki", "village": "Thodupuzha"},
    {"state": "Kerala", "district": "Ernakulam", "village": "Perumbavoor"},
    # Uttarakhand
    {"state": "Uttarakhand", "district": "Dehradun", "village": "Vikas Nagar"},
    {"state": "Uttarakhand", "district": "Haridwar", "village": "Laksar"},
    {"state": "Uttarakhand", "district": "Udham Singh Nagar", "village": "Jaspur"},
    {"state": "Uttarakhand", "district": "Nainital", "village": "Haldwani"},
    # Himachal Pradesh
    {"state": "Himachal Pradesh", "district": "Kangra", "village": "Palampur"},
    {"state": "Himachal Pradesh", "district": "Mandi", "village": "Sundernagar"},
    {"state": "Himachal Pradesh", "district": "Shimla", "village": "Theog"},
    {"state": "Himachal Pradesh", "district": "Kullu", "village": "Banjar"},
    # Jammu & Kashmir
    {"state": "Jammu and Kashmir", "district": "Anantnag", "village": "Bijbehara"},
    {"state": "Jammu and Kashmir", "district": "Baramulla", "village": "Sopore"},
    {"state": "Jammu and Kashmir", "district": "Jammu", "village": "Akhnoor"},
]


def get_random_location() -> dict:
    """Fetch a random Indian village from Photon geocoder API.

    Queries Photon with a random seed, picks a random village from results.
    Falls back to the hardcoded LOCATIONS list if the API call fails.
    """
    query = random.choice(_QUERY_SEEDS)
    try:
        resp = requests.get(_PHOTON_API, params={
            "q": query,
            "osm_tag": "place:village",
            "limit": 100,
            "bbox": _INDIA_BBOX,
            "lang": "en",
        }, timeout=5)
        resp.raise_for_status()
        features = resp.json().get("features", [])

        # Filter to valid Indian villages with name, state, and district
        valid = []
        for f in features:
            props = f.get("properties", {})
            name = props.get("name")
            state = props.get("state")
            district = props.get("county") or props.get("city")
            country = props.get("country", "")
            if name and state and district and "India" in country:
                valid.append({"state": state, "district": district, "village": name})

        if valid:
            return random.choice(valid)
    except Exception:
        pass

    # Fallback to hardcoded list
    return random.choice(LOCATIONS)


# ─── Mandi Names ───────────────────────────────────────────────────────────────

MANDI_NAMES = [
    "Indore Mandi", "Bhopal Mandi", "Ujjain Mandi", "Dewas Mandi",
    "Lucknow Mandi", "Kanpur Mandi", "Agra Mandi", "Varanasi Mandi",
    "Jaipur Mandi", "Jodhpur Mandi", "Kota Mandi", "Ajmer Mandi",
    "Pune Mandi", "Nagpur Mandi", "Nashik Mandi", "Aurangabad Mandi",
    "Ahmedabad Mandi", "Rajkot Mandi", "Surat Mandi", "Vadodara Mandi",
    "Ludhiana Mandi", "Amritsar Mandi", "Patiala Mandi", "Bathinda Mandi",
    "Karnal Mandi", "Hisar Mandi", "Sirsa Mandi", "Rohtak Mandi",
    "Patna Mandi", "Muzaffarpur Mandi", "Darbhanga Mandi",
    "Guntur Mandi", "Warangal Mandi", "Coimbatore Mandi",
]


# ─── Crop Price Ranges (INR per quintal) ───────────────────────────────────────

CROP_PRICE_RANGES: dict[str, Tuple[int, int]] = {
    "Wheat": (2000, 2800),
    "Paddy(Common)": (1800, 2600),
    "Rice": (2800, 4200),
    "Maize": (1600, 2400),
    "Jowar(Sorghum)": (2200, 3500),
    "Bengal Gram(Gram)(Whole)": (4500, 6500),
    "Red Gram": (5500, 8000),
    "Black Gram(Urd Beans)(Whole)": (5000, 7500),
    "Green Gram(Moong)(Whole)": (6000, 8500),
    "Groundnut": (4500, 6500),
    "Sesamum(Sesame,Gingelly,Til)": (8000, 14000),
    "Mustard": (4500, 6500),
    "Soyabean": (3800, 5500),
    "Sunflower": (4500, 6500),
    "Cotton": (5500, 8000),
    "Jute": (3500, 5500),
    "Apple": (3000, 8000),
    "Orange": (2000, 5000),
    "Banana": (1000, 3000),
    "Mango": (2000, 6000),
    "Onion": (800, 3500),
    "Potato": (600, 2000),
    "Garlic": (3000, 10000),
    "Chili Red": (8000, 18000),
    "Ginger(Dry)": (5000, 12000),
    "Bajra(Pearl Millet/Cumbu)": (1800, 2800),
    "Barley(Jau)": (1500, 2200),
    "Ragi(Finger Millet)": (2800, 4000),
    "Sugarcane": (280, 380),
    "Turmeric": (6000, 12000),
}

# Default price range for commodities not in the dict
DEFAULT_PRICE_RANGE = (2000, 5000)

# Commodity-specific varieties (key matches CROP_PRICE_RANGES names)
COMMODITY_VARIETIES: dict[str, list[str]] = {
    "Wheat": ["Sharbati", "Lokwan", "MP Wheat", "Dara", "Raj 3765", "PBW 343", "Local"],
    "Paddy(Common)": ["Swarna", "IR-64", "Sona Masuri", "BPT 5204", "HMT", "Local"],
    "Rice": ["Basmati", "Sona Masuri", "Ponni", "HMT", "IR-64", "Kolam"],
    "Maize": ["Yellow", "White", "Hybrid", "Desi", "Pop Corn", "Local"],
    "Jowar(Sorghum)": ["Maldandi", "Hybrid", "Yellow", "White", "Local"],
    "Bengal Gram(Gram)(Whole)": ["Desi", "Kabuli", "Bold", "Medium", "Local"],
    "Red Gram": ["Tur Dal", "Bold", "Medium", "Local"],
    "Black Gram(Urd Beans)(Whole)": ["Bold", "Medium", "Desi", "Local"],
    "Green Gram(Moong)(Whole)": ["Bold", "Medium", "Desi", "Local"],
    "Groundnut": ["Bold", "Java", "TJ", "G-20", "Local"],
    "Sesamum(Sesame,Gingelly,Til)": ["White", "Black", "Brown", "Local"],
    "Mustard": ["Laha", "Sarson", "Rai", "Bold", "Local"],
    "Soyabean": ["Yellow", "JS 335", "JS 9560", "Local"],
    "Sunflower": ["Hybrid", "Morden", "Local"],
    "Cotton": ["H-4", "Shankar-6", "MCU-5", "DCH-32", "Medium Staple", "Long Staple"],
    "Jute": ["TD-5", "JRO 524", "White", "Tossa", "Local"],
    "Onion": ["Nasik Red", "Bellary", "Poona", "White", "Local"],
    "Potato": ["Jyoti", "Kufri", "Chandramukhi", "Pukhraj", "Local"],
    "Garlic": ["Desi", "Hybrid", "Single Clove", "Local"],
    "Chili Red": ["Guntur", "Byadgi", "Teja", "Kashmiri", "Local"],
    "Turmeric": ["Erode", "Salem", "Nizamabad", "Rajapuri", "Local"],
    "Apple": ["Royal Delicious", "Golden", "Red Delicious", "Shimla", "Kashmiri"],
    "Orange": ["Nagpur", "Kinnow", "Mosambi", "Local"],
    "Banana": ["Robusta", "Poovan", "Nendran", "Elakki", "Local"],
    "Mango": ["Alphonso", "Dasheri", "Langra", "Totapuri", "Kesar", "Local"],
    "Sugarcane": ["Co 86032", "CoC 671", "Co 0238", "Local"],
    "Bajra(Pearl Millet/Cumbu)": ["Hybrid", "Desi", "Bold", "Local"],
    "Barley(Jau)": ["Feed", "Malt", "Desi", "Local"],
    "Ragi(Finger Millet)": ["Local", "Hybrid", "Bold"],
    "Ginger(Dry)": ["Maran", "Nadia", "Ernad", "Local"],
}
DEFAULT_VARIETIES = ["Local", "Desi", "Hybrid", "FAQ"]

GRADES = ["FAQ", "Non-FAQ", "Average", "Superior", "Ordinary"]


# ─── Weather Ranges ─────────────────────────────────────────────────────────────

WEATHER_CONDITIONS = [
    "Clear Sky", "Partly Cloudy", "Mostly Cloudy", "Overcast",
    "Light Rain", "Moderate Rain", "Heavy Rain", "Thunderstorm",
    "Haze", "Fog", "Mist", "Sunny",
]

WIND_DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def random_temp(season: str = "summer") -> Tuple[float, float]:
    """Return (min_temp, max_temp) based on season."""
    ranges = {
        "summer": (28, 45),
        "winter": (5, 25),
        "monsoon": (22, 36),
        "spring": (18, 35),
    }
    lo, hi = ranges.get(season, (20, 38))
    min_t = round(random.uniform(lo, lo + (hi - lo) * 0.4), 1)
    max_t = round(random.uniform(min_t + 3, hi), 1)
    return min_t, max_t


def random_humidity() -> int:
    return random.randint(30, 95)


def random_rainfall() -> float:
    # 60% chance of no rain
    if random.random() < 0.6:
        return 0.0
    return round(random.uniform(0.5, 100.0), 1)


def random_wind_speed() -> float:
    return round(random.uniform(5, 40), 1)


# ─── Soil Health Card Data ──────────────────────────────────────────────────────

SHC_SOIL_TYPES = [
    "Sandy Soil", "Sandy Loam", "Loamy Soil", "Clay Loam",
    "Clay Soil", "Silty Clay", "Silty Loam", "Red Soil",
    "Black Cotton Soil", "Alluvial Soil", "Laterite Soil",
]

# Crop fertilizer recommendation templates for SHC reports
# Amounts are base values in Kg Per Acre; tool adds ±2 kg randomization
SHC_CROP_FERTILIZERS = [
    {"crop": "Paddy (Local Variety)", "combo1": {"DAP": 17, "Urea": 45}, "combo2": {"SSP": 50, "Urea": 52}},
    {"crop": "Wheat (Desi)", "combo1": {"DAP": 17, "MOP": 10, "Urea": 32}, "combo2": {"SSP": 50, "MOP": 10, "Urea": 39}},
    {"crop": "Maize (Local)", "combo1": {"DAP": 17, "MOP": 18, "Urea": 45}, "combo2": {"SSP": 50, "MOP": 18, "Urea": 52}},
    {"crop": "Sunflower", "combo1": {"DAP": 26, "Urea": 42}, "combo2": {"SSP": 75, "Urea": 52}},
    {"crop": "Sugarcane (Planted)", "combo1": {"DAP": 22, "MOP": 22, "Urea": 136}, "combo2": {"SSP": 63, "MOP": 22, "Urea": 145}},
    {"crop": "Mustard (Irrigated)", "combo1": {"DAP": 13, "Urea": 47}, "combo2": {"SSP": 38, "Urea": 52}},
    {"crop": "Cotton (Hybrid)", "combo1": {"DAP": 22, "MOP": 15, "Urea": 55}, "combo2": {"SSP": 63, "MOP": 15, "Urea": 62}},
    {"crop": "Soybean", "combo1": {"DAP": 33, "Urea": 10}, "combo2": {"SSP": 95, "Urea": 15}},
    {"crop": "Gram (Desi)", "combo1": {"DAP": 22, "Urea": 10}, "combo2": {"SSP": 63, "Urea": 15}},
    {"crop": "Groundnut", "combo1": {"DAP": 22, "MOP": 10, "Urea": 15}, "combo2": {"SSP": 63, "MOP": 10, "Urea": 20}},
    {"crop": "Bajra (Pearl Millet)", "combo1": {"DAP": 17, "Urea": 35}, "combo2": {"SSP": 50, "Urea": 42}},
    {"crop": "Jowar (Sorghum)", "combo1": {"DAP": 17, "MOP": 8, "Urea": 35}, "combo2": {"SSP": 50, "MOP": 8, "Urea": 42}},
    {"crop": "Potato", "combo1": {"DAP": 33, "MOP": 25, "Urea": 55}, "combo2": {"SSP": 95, "MOP": 25, "Urea": 62}},
    {"crop": "Onion", "combo1": {"DAP": 22, "MOP": 15, "Urea": 45}, "combo2": {"SSP": 63, "MOP": 15, "Urea": 52}},
    {"crop": "Tomato", "combo1": {"DAP": 26, "MOP": 20, "Urea": 50}, "combo2": {"SSP": 75, "MOP": 20, "Urea": 57}},
    {"crop": "Barley", "combo1": {"DAP": 17, "Urea": 28}, "combo2": {"SSP": 50, "Urea": 35}},
    {"crop": "Lentil (Masoor)", "combo1": {"DAP": 22, "Urea": 8}, "combo2": {"SSP": 63, "Urea": 12}},
]

SHC_ORGANIC_REC = (
    "FYM: 12 Tonne Per Hectare\n"
    "Compost / Enriched Compost: 6.0 Tonne Per Hectare\n"
    "Vermicompost: 3.0 Tonne Per Hectare\n"
    "Oil Cake (Neem/Castor/Karanja): 0.80 Tonne Per Hectare\n"
    "Bio-fertilizers: Rhizobium (for legumes) / Azotobacter + PSB @ 2 kg/ha"
)


# ─── PM-KISAN Status Data ──────────────────────────────────────────────────────

INSTALLMENT_AMOUNTS = [2000, 2000, 2000]  # 3 installments per year

PAYMENT_MODES = ["Aadhaar", "Account Number"]

BANK_NAMES = [
    "State Bank of India", "Punjab National Bank", "Bank of Baroda",
    "Canara Bank", "Union Bank of India", "Indian Bank",
    "Central Bank of India", "Bank of India", "UCO Bank",
    "Indian Overseas Bank", "IDBI Bank",
]


# ─── PMFBY Status Data ─────────────────────────────────────────────────────────

SEASONS = ["Kharif", "Rabi", "Summer"]
PMFBY_CROPS = ["Paddy", "Wheat", "Cotton", "Soybean", "Maize", "Groundnut", "Jowar", "Bajra"]
CLAIM_STATUSES = ["Approved", "Under Assessment", "Settled", "Rejected", "Pending"]
POLICY_STATUSES = ["Active", "Expired", "Cancelled", "Under Review"]


# ─── Grievance Data ─────────────────────────────────────────────────────────────

GRIEVANCE_STATUSES = ["Pending", "Under Review", "Resolved", "Forwarded to State"]

OFFICER_REPLIES = [
    "Your grievance has been noted and is being processed. Please wait for resolution.",
    "We are looking into your complaint. Expected resolution within 15 working days.",
    "Your issue has been forwarded to the concerned district officer.",
    "The matter has been resolved. Please check your bank account for updated status.",
    "We need additional documents. Please visit your nearest CSC center.",
    "Your registration details have been corrected. Changes will reflect in the next cycle.",
]


# ─── Scenario Definitions (loaded from assets/scenarios.json) ─────────────────

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def load_scenarios(path: Path | None = None) -> list[dict]:
    """Load scenario definitions from the JSON file."""
    path = path or _ASSETS_DIR / "scenarios.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


SCENARIOS: list[dict] = load_scenarios()

ADVERSARIAL_SCENARIO_IDS = {s["id"] for s in SCENARIOS if s["id"].startswith("adversarial_")}


# ─── Farmer Crops (common crops a farmer would grow) ─────────────────────────

FARMER_CROPS = [
    # Cereals & millets
    "Wheat", "Paddy", "Rice", "Maize", "Jowar", "Bajra", "Barley", "Ragi",
    "Foxtail Millet", "Kodo Millet", "Little Millet", "Barnyard Millet",
    # Pulses
    "Gram", "Lentil", "Red Gram", "Black Gram", "Green Gram",
    "Cowpea", "Kidney Beans", "Field Pea",
    # Oilseeds
    "Groundnut", "Mustard", "Soybean", "Sunflower", "Sesamum",
    "Castor Seed", "Linseed", "Niger Seed",
    # Cash crops
    "Cotton", "Sugarcane", "Jute", "Tobacco", "Coffee", "Tea", "Rubber",
    # Spices
    "Chili", "Turmeric", "Ginger", "Garlic", "Coriander", "Cumin",
    "Black pepper", "Cardamom", "Cloves", "Fenugreek",
    # Vegetables
    "Onion", "Potato", "Tomato", "Brinjal", "Cauliflower", "Cabbage",
    "Capsicum", "Bitter gourd", "Bottle gourd", "Ridge Gourd",
    "Pumpkin", "Cucumber", "Carrot", "Radish", "Beetroot",
    "Green Peas", "Beans", "Ladies Finger", "Spinach", "Drumstick",
    # Fruits
    "Banana", "Mango", "Orange", "Apple", "Papaya", "Guava",
    "Pomegranate", "Grapes", "Litchi", "Watermelon", "Coconut",
    "Pineapple", "Custard Apple", "Chikoo", "Jackfruit", "Lemon",
]


# ─── Mood & Language Weights ─────────────────────────────────────────────────

MOOD_WEIGHTS = {
    "normal": 0.75,
    "frustrated": 0.23,
    "adversarial": 0.02,
}

LANGUAGE_WEIGHTS = {
    "hi": 0.55,
    "en": 0.15,
    "ta": 0.04,   # Tamil
    "te": 0.04,   # Telugu
    "bn": 0.04,   # Bengali
    "mr": 0.04,   # Marathi
    "gu": 0.03,   # Gujarati
    "kn": 0.03,   # Kannada
    "pa": 0.03,   # Punjabi
    "or": 0.02,   # Odia
    "ml": 0.02,   # Malayalam
    "as": 0.01,   # Assamese
}

LATIN_SCRIPT_PROBABILITY = 0.05
SAME_LANGUAGE_PROBABILITY = 0.90
LANGUAGE_SWITCH_PROBABILITY = 0.015  # ~1.5% of conversations switch target language mid-conversation

TARGET_LANGUAGE_WEIGHTS = {
    "hi": 0.25,
    "bn": 0.10,   # Bengali
    "te": 0.10,   # Telugu
    "mr": 0.10,   # Marathi
    "ta": 0.09,   # Tamil
    "gu": 0.08,   # Gujarati
    "kn": 0.08,   # Kannada
    "ml": 0.07,   # Malayalam
    "en": 0.07,
    "as": 0.06,   # Assamese
}