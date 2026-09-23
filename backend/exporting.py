"""Pure transcript serializers shared by HTTP and command-line interfaces."""
import csv
import io

from backend.quality import reading_segments, subtitle_segments


FORMATS = {"txt", "srt", "vtt", "md", "csv", "json"}
TEXT_SUFFIXES = {"txt": "txt", "srt": "srt", "vtt": "vtt", "md": "md", "csv": "csv"}


def format_timestamp(seconds: float, fmt: str = "srt") -> str:
    total_ms = max(0, round(seconds * 1000))
    total_seconds, ms = divmod(total_ms, 1000)
    hours, remaining = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{',' if fmt == 'srt' else '.'}{ms:03d}"


def render(segments: list[dict], fmt: str, variant: str = "speakers") -> tuple[str, str]:
    """Return serialized transcript and MIME type. JSON is intentionally CLI-owned."""
    if fmt not in TEXT_SUFFIXES:
        raise ValueError(f"Formato non supportato: {fmt}")
    if variant not in ("raw", "speakers"):
        raise ValueError("Variante export non supportata")

    include_speaker = variant == "speakers"
    prepared = segments
    if fmt in ("srt", "vtt"):
        prepared = subtitle_segments(segments, include_speaker=include_speaker)
    elif include_speaker:
        prepared = reading_segments(segments)

    if fmt == "txt":
        lines = []
        for segment in prepared:
            prefix = f"[{format_timestamp(segment['start'], 'plain')} → {format_timestamp(segment['end'], 'plain')}]"
            speaker = segment.get("speaker") if include_speaker else None
            lines.append(f"{prefix} {speaker}: {segment['text']}" if speaker else f"{prefix} {segment['text']}")
        return "\n".join(lines), "text/plain"

    if fmt == "srt":
        lines = []
        for index, segment in enumerate(prepared, 1):
            lines.extend((str(index), f"{format_timestamp(segment['start'])} --> {format_timestamp(segment['end'])}", segment["text"], ""))
        return "\n".join(lines), "text/srt"

    if fmt == "vtt":
        lines = ["WEBVTT", ""]
        for segment in prepared:
            lines.extend((f"{format_timestamp(segment['start'], 'vtt')} --> {format_timestamp(segment['end'], 'vtt')}", segment["text"], ""))
        return "\n".join(lines), "text/vtt"

    if fmt == "md":
        lines = ["# Trascrizione\n"]
        current_speaker = None
        for segment in prepared:
            if include_speaker:
                speaker = segment.get("speaker") or "—"
                if speaker != current_speaker:
                    lines.append(f"\n**{speaker}**")
                    current_speaker = speaker
            lines.append(f"*[{format_timestamp(segment['start'], 'plain')}]* {segment['text']}")
        return "\n".join(lines), "text/markdown"

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    columns = ["id", "start", "end", "text"] if not include_speaker else ["id", "start", "end", "speaker", "text"]
    writer.writerow(columns)
    for segment in prepared:
        row = [segment.get("id", ""), segment["start"], segment["end"]]
        if include_speaker:
            row.append(segment.get("speaker", ""))
        writer.writerow([*row, segment["text"]])
    return buffer.getvalue(), "text/csv"
