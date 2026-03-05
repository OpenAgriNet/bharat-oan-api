"use client";

import type { FileInfo } from "@/lib/types";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

interface FileSelectorProps {
  files: FileInfo[];
  selected: string | null;
  onSelect: (filename: string) => void;
}

export function FileSelector({ files, selected, onSelect }: FileSelectorProps) {
  return (
    <Select value={selected ?? undefined} onValueChange={onSelect}>
      <SelectTrigger className="w-[400px]">
        <SelectValue placeholder="Select a JSONL file..." />
      </SelectTrigger>
      <SelectContent>
        {files.map((f) => (
          <SelectItem key={f.name} value={f.name}>
            {f.name}
            <span className="ml-2 text-muted-foreground">
              ({formatSize(f.size)} &middot; {formatDate(f.modified)})
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
