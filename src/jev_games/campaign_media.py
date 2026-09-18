"""Incremental video chapters with exact-frame decision subtitles."""
import json
from fractions import Fraction
from pathlib import Path
import subprocess

from run import write


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
        '-show_format', '-of', 'json', str(path)], text=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))


def ass_time(seconds):
    n = max(0, round(seconds*100))
    return f'{n//360000}:{n//6000%60:02d}:{n//100%60:02d}.{n%100:02d}'


def media_timeout(duration):
    """Allow complete campaigns to encode; short chapters retain their old floor."""
    return max(180, int(duration*3)+60)


def finalize(chapter, events, start_timeline, end_timeline):
    chapter = Path(chapter).resolve()
    sources = list(chapter.glob('session*.nut'))
    if len(sources) != 1:
        raise RuntimeError('Expected one recorded NUT for this chapter')
    source = sources[0]
    raw = probe(source)
    video = next(s for s in raw['streams'] if s['codec_type'] == 'video')
    fps = Fraction(video['r_frame_rate'])
    # NUT may not report nb_frames; count decoded frames once if needed.
    count = video.get('nb_frames')
    if not count or count == 'N/A':
        counted = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
            '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(source)], text=True))
        count = counted['streams'][0]['nb_read_frames']
    offset = end_timeline-int(count)
    trim_frames = max(0, start_timeline-offset)
    duration = float((int(count)-trim_frames)/fps)
    if duration <= 0:
        return None
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 768
PlayResY: 800
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,19,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,1,12,12,12,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = [header]
    for event in events:
        start = float((event['timeline_start']-start_timeline)/fps)
        end = float((event['timeline_end']-start_timeline)/fps)
        if end <= start:
            continue
        label = f"{event['controller']} | {event.get('level', '?')} | {event['action']} | {event['frames']} frames"
        second = f"Decision {event.get('decision', '-')} | checkpoints restored: {event.get('rewinds', 0)}"
        text = (label+'\\N'+second).replace('{', '(').replace('}', ')')
        lines.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}\n')
    (chapter/'decisions.ass').write_text(''.join(lines), encoding='utf-8')
    write(chapter/'video_events.json', events)
    vf = f'trim=start_frame={trim_frames},setpts=PTS-STARTPTS,scale=768:720:flags=neighbor,pad=768:800:0:0:black,ass=decisions.ass,format=yuv420p'
    af = f'atrim=start={float(trim_frames/fps)},asetpts=PTS-STARTPTS,apad,atrim=duration={duration}'
    command = ['ffmpeg', '-hide_banner', '-v', 'error', '-i', str(source), '-vf', vf, '-af', af,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
        str(chapter/'decisions.mp4')]
    with (chapter/'conversion.log').open('w', encoding='utf-8') as log:
        subprocess.run(command, cwd=chapter, stdout=log, stderr=log, check=True, timeout=media_timeout(duration),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    metadata = probe(chapter/'decisions.mp4')
    output_video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    if int(output_video['nb_frames']) != end_timeline-start_timeline:
        raise RuntimeError('Encoded chapter frame count differs from recorded controller timeline')
    if not any(s['codec_type'] == 'audio' for s in metadata['streams']):
        raise RuntimeError('Chapter is missing audio')
    subprocess.run(['ffmpeg', '-hide_banner', '-v', 'error', '-i', str(chapter/'decisions.mp4'),
        '-f', 'null', '-'], check=True, timeout=media_timeout(duration), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result = {'file': str((chapter/'decisions.mp4').resolve()), 'duration': duration,
        'timeline_start': start_timeline, 'timeline_end': end_timeline,
        'boot_frames_removed': trim_frames, 'video_decode_verified': True, 'has_audio': True,
        'raw_removed_after_verification': True}
    write(chapter/'video.json', result)
    # Only the raw file created by this chapter, after its MP4 was verified.
    if source.resolve().parent != chapter or source.suffix != '.nut':
        raise RuntimeError('Unexpected raw recording path')
    source.unlink()
    return result
