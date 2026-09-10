"""Kdenlive CLI - Export module: JSON to MLT/Kdenlive XML generation and rendering."""

import os
import tempfile
from typing import Dict, Any, List, Optional
from cli_anything.kdenlive.utils.mlt_xml import (
    xml_escape,
    seconds_to_frames,
    build_mlt_xml,
)


RENDER_PRESETS = {
    "h264_hq": {
        "description": "H.264 High Quality",
        "vcodec": "libx264",
        "acodec": "aac",
        "vbitrate": "8000k",
        "abitrate": "192k",
        "extension": "mp4",
    },
    "h264_fast": {
        "description": "H.264 Fast/Draft",
        "vcodec": "libx264",
        "acodec": "aac",
        "vbitrate": "4000k",
        "abitrate": "128k",
        "extension": "mp4",
    },
    "h265_hq": {
        "description": "H.265/HEVC High Quality",
        "vcodec": "libx265",
        "acodec": "aac",
        "vbitrate": "6000k",
        "abitrate": "192k",
        "extension": "mp4",
    },
    "webm_vp9": {
        "description": "WebM VP9",
        "vcodec": "libvpx-vp9",
        "acodec": "libvorbis",
        "vbitrate": "5000k",
        "abitrate": "192k",
        "extension": "webm",
    },
    "prores": {
        "description": "Apple ProRes 422",
        "vcodec": "prores_ks",
        "acodec": "pcm_s16le",
        "vbitrate": "0",
        "abitrate": "0",
        "extension": "mov",
    },
    "lossless": {
        "description": "FFV1 Lossless",
        "vcodec": "ffv1",
        "acodec": "flac",
        "vbitrate": "0",
        "abitrate": "0",
        "extension": "mkv",
    },
    "gif": {
        "description": "Animated GIF",
        "vcodec": "gif",
        "acodec": "none",
        "vbitrate": "0",
        "abitrate": "0",
        "extension": "gif",
    },
    "audio_only": {
        "description": "Audio Only (WAV)",
        "vcodec": "none",
        "acodec": "pcm_s16le",
        "vbitrate": "0",
        "abitrate": "0",
        "extension": "wav",
    },
}


def generate_kdenlive_xml(project: Dict[str, Any]) -> str:
    """Generate valid Kdenlive/MLT XML from the JSON project.

    Returns the XML string.
    """
    return build_mlt_xml(project)


def _timeline_duration(project: Dict[str, Any]) -> int:
    """Longest track duration in frames, using the XML builder's own maths.

    Zero means nothing on the timeline has a positive duration, which is the
    case build_mlt_xml papers over with a 300-second fallback.
    """
    from cli_anything.kdenlive.utils.mlt_xml import _compute_track_duration

    profile = project.get("profile", {})
    fps_num = profile.get("fps_num", 30)
    fps_den = profile.get("fps_den", 1)
    return max(
        (_compute_track_duration(t, fps_num, fps_den)
         for t in project.get("tracks", [])),
        default=0,
    )


def render_project(
    project: Dict[str, Any],
    output_path: str,
    preset: str = "h264_hq",
    overwrite: bool = False,
    timeout: int = 300,
    keep_mlt: Optional[str] = None,
) -> Dict[str, Any]:
    """Render the project to a video file using the real melt renderer.

    The project is serialised to MLT XML, then handed to melt, which applies
    every project-level filter and transition. Rendering with a tool that only
    reads the raw source clips would silently drop them.

    Args:
        project: The project dict
        output_path: Output video file path
        preset: Name of a preset in RENDER_PRESETS
        overwrite: Allow overwriting an existing output file
        timeout: Maximum seconds to wait for melt
        keep_mlt: If set, write the intermediate MLT XML here and keep it

    Returns:
        Dict with output path, file size, codecs, preset and method
    """
    if preset not in RENDER_PRESETS:
        raise ValueError(
            f"Unknown preset: {preset}. "
            f"Available: {', '.join(sorted(RENDER_PRESETS))}"
        )
    p = RENDER_PRESETS[preset]

    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(f"Output file exists: {output_path}. Use --overwrite.")

    # An empty timeline has no duration of its own, and build_mlt_xml falls
    # back to 300s — so rendering a fresh project would silently encode five
    # minutes of black video, often running to the timeout.
    if not _timeline_duration(project):
        raise ValueError(
            "Timeline is empty — nothing to render. "
            "Add at least one clip with a positive duration first."
        )

    from cli_anything.kdenlive.utils import melt_backend

    xml = generate_kdenlive_xml(project)

    if keep_mlt:
        mlt_path = os.path.abspath(keep_mlt)
        os.makedirs(os.path.dirname(mlt_path), exist_ok=True)
        with open(mlt_path, "w") as f:
            f.write(xml)
        cleanup = False
    else:
        fd, mlt_path = tempfile.mkstemp(suffix=".mlt", prefix="kdenlive_render_")
        with os.fdopen(fd, "w") as f:
            f.write(xml)
        cleanup = True

    # A preset codec of "none" means "this stream is disabled", not a codec
    # name, so it must never reach the backend's codec allowlist — melt takes
    # vn=1 / an=1 for that. Bitrates are what separate the quality presets,
    # so they have to be forwarded too or h264_hq and h264_fast encode alike.
    vcodec = p["vcodec"]
    acodec = p["acodec"]
    extra_args = []

    if vcodec == "none":
        vcodec = ""
        extra_args.append("vn=1")
    elif p.get("vbitrate") not in ("0", "", None):
        extra_args.append(f"vb={p['vbitrate']}")

    if acodec == "none":
        acodec = ""
        extra_args.append("an=1")
    elif p.get("abitrate") not in ("0", "", None):
        extra_args.append(f"ab={p['abitrate']}")

    # melt can leave a partial file behind if it is killed or errors after
    # opening the consumer. Removing an output we created keeps a retry with
    # a higher --timeout from being refused by the existence check.
    output_pre_existed = os.path.exists(output_path)

    try:
        result = melt_backend.render_mlt(
            mlt_path, output_path,
            vcodec=vcodec, acodec=acodec,
            overwrite=overwrite, timeout=timeout,
            extra_args=extra_args or None,
        )
    except BaseException:
        if not output_pre_existed and os.path.exists(output_path):
            os.unlink(output_path)
        raise
    finally:
        if cleanup and os.path.exists(mlt_path):
            os.unlink(mlt_path)

    result.update({
        "preset": preset,
        "vcodec": p["vcodec"],
        "acodec": p["acodec"],
        "extra_args": extra_args,
    })
    if keep_mlt:
        result["mlt_path"] = mlt_path
    return result


def list_render_presets() -> List[Dict[str, Any]]:
    """List available render presets."""
    result = []
    for name, p in RENDER_PRESETS.items():
        result.append({
            "name": name,
            "description": p["description"],
            "vcodec": p["vcodec"],
            "acodec": p["acodec"],
            "extension": p["extension"],
        })
    return result
