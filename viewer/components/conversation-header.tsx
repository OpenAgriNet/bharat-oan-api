import type { ConversationRecord } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  MapPin,
  Sprout,
  Languages,
  Calendar,
  User,
  Hash,
} from "lucide-react";

const langLabels: Record<string, string> = {
  hi: "Hindi",
  en: "English",
  hinglish: "Hinglish",
  ta: "Tamil",
  te: "Telugu",
  bn: "Bengali",
  mr: "Marathi",
  gu: "Gujarati",
  kn: "Kannada",
  pa: "Punjabi",
  or: "Odia",
  ml: "Malayalam",
};

interface ConversationHeaderProps {
  record: ConversationRecord;
}

export function ConversationHeader({ record }: ConversationHeaderProps) {
  const { profile, env } = record;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <User className="size-5" />
          {profile.name}
          <Badge variant="secondary" className="ml-2">
            {profile.mood}
          </Badge>
        </CardTitle>
        <CardDescription>
          {profile.scenario.description_en}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <InfoItem
            icon={<MapPin className="size-4" />}
            label="Location"
            value={`${profile.village}, ${profile.district}, ${profile.state}`}
          />
          <InfoItem
            icon={<Sprout className="size-4" />}
            label="Crops"
            value={profile.crops.join(", ")}
          />
          <InfoItem
            icon={<Languages className="size-4" />}
            label="User Language"
            value={langLabels[profile.language] ?? profile.language}
          />
          <InfoItem
            icon={<Languages className="size-4" />}
            label="Target Language"
            value={langLabels[env.target_language] ?? env.target_language}
          />
          <InfoItem
            icon={<Hash className="size-4" />}
            label="Verbosity"
            value={profile.verbosity}
          />
          <InfoItem
            icon={<Calendar className="size-4" />}
            label="Sim Date"
            value={new Date(env.today_date).toLocaleDateString()}
          />
          <InfoItem
            icon={<Hash className="size-4" />}
            label="Scenario"
            value={`${profile.scenario.category} / ${profile.scenario.id}`}
          />
          <InfoItem
            icon={<Sprout className="size-4" />}
            label="Land"
            value={`${profile.land_acres} acres`}
          />
        </div>
        <div className="mt-4 flex gap-2 text-xs text-muted-foreground">
          <span>Model: {env.agrinet_model}</span>
          <span>&middot;</span>
          <span>Turns: {record.turn_count}</span>
          <span>&middot;</span>
          <span>
            {record.completed
              ? "Completed"
              : record.error
              ? "Error"
              : "Incomplete"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function InfoItem({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-muted-foreground mt-0.5">{icon}</span>
      <div>
        <div className="text-muted-foreground text-xs">{label}</div>
        <div>{value}</div>
      </div>
    </div>
  );
}
