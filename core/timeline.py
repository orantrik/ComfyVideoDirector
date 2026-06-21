"""Timecode + segment time-range helpers."""


def format_timecode(seconds: int) -> str:
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def build_segment_time_ranges(total_duration_seconds=240, segment_duration_seconds=8):
    segments = []
    total_segments = total_duration_seconds // segment_duration_seconds
    for index in range(total_segments):
        start_seconds = index * segment_duration_seconds
        end_seconds = start_seconds + segment_duration_seconds
        segments.append({
            "segment_id": index + 1,
            "time_start": format_timecode(start_seconds),
            "time_end": format_timecode(end_seconds),
            "duration_seconds": segment_duration_seconds,
        })
    return segments
