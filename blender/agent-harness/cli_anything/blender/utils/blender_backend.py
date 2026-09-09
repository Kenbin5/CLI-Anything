"""Blender backend — invoke Blender headless for rendering.

Requires: blender (system package)
    apt install blender
"""

import os
import shutil
import subprocess
import tempfile
from typing import Optional


def find_blender() -> str:
    """Find the Blender executable. Raises RuntimeError if not found."""
    for name in ("blender",):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "Blender is not installed. Install it with:\n"
        "  apt install blender   # Debian/Ubuntu\n"
        "  brew install --cask blender  # macOS"
    )


def get_version() -> str:
    """Get the installed Blender version string."""
    blender = find_blender()
    result = subprocess.run(
        [blender, "--version"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip().split("\n")[0]


def render_script(
    script_path: str,
    timeout: int = 300,
) -> dict:
    """Run a bpy script using Blender headless.

    Args:
        script_path: Path to the Python script to execute
        timeout: Maximum seconds to wait

    Returns:
        Dict with stdout, stderr, return code
    """
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")

    blender = find_blender()
    cmd = [blender, "--background", "--python", script_path]

    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        timeout=timeout,
    )

    return {
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def resolve_output(output_path: str) -> Optional[str]:
    """Return the real rendered file, or None if nothing was produced.

    Blender appends a frame number to the output path for single frames,
    e.g. /tmp/render.png becomes /tmp/render0001.png.
    """
    if os.path.exists(output_path):
        return output_path
    base, ext = os.path.splitext(output_path)
    for suffix in ("0001", "0000", "1"):
        candidate = f"{base}{suffix}{ext}"
        if os.path.exists(candidate):
            return candidate
    return None


def render_script_file(
    script_path: str,
    output_path: str,
    timeout: int = 300,
    animation: bool = False,
) -> dict:
    """Render an on-disk bpy script with Blender headless and verify the output.

    Args:
        script_path: Path to the bpy script to execute
        output_path: Expected output file, or the frame-sequence base for animation
        timeout: Maximum seconds to wait
        animation: True when the script renders a frame range

    Returns:
        Dict with output path, file size, method, blender version, command
    """
    result = render_script(script_path, timeout=timeout)

    if result["returncode"] != 0:
        raise RuntimeError(
            f"Blender render failed (exit {result['returncode']}):\n"
            f"  stderr: {result['stderr'][-500:]}"
        )

    if animation:
        base, ext = os.path.splitext(os.path.abspath(output_path))
        frame_dir = os.path.dirname(base) or "."
        prefix = os.path.basename(base)
        frames = sorted(
            f for f in os.listdir(frame_dir)
            if f.startswith(prefix) and f.endswith(ext)
        )
        if not frames:
            raise RuntimeError(
                f"Blender render produced no frames.\n"
                f"  Expected: {output_path}\n"
                f"  stdout: {result['stdout'][-500:]}"
            )
        return {
            "output": frame_dir,
            "frames": len(frames),
            "first_frame": os.path.join(frame_dir, frames[0]),
            "format": ext.lstrip("."),
            "method": "blender-headless",
            "blender_version": get_version(),
            "command": result["command"],
        }

    actual_output = resolve_output(output_path)
    if actual_output is None:
        raise RuntimeError(
            f"Blender render produced no output file.\n"
            f"  Expected: {output_path}\n"
            f"  stdout: {result['stdout'][-500:]}"
        )

    return {
        "output": os.path.abspath(actual_output),
        "format": os.path.splitext(actual_output)[1].lstrip("."),
        "method": "blender-headless",
        "blender_version": get_version(),
        "file_size": os.path.getsize(actual_output),
        "command": result["command"],
    }


def render_scene_headless(
    bpy_script_content: str,
    output_path: str,
    timeout: int = 300,
) -> dict:
    """Write a bpy script to a temp file and render with Blender headless.

    Args:
        bpy_script_content: The bpy Python script as a string
        output_path: Expected output path (set in the script)
        timeout: Maximum seconds to wait

    Returns:
        Dict with output path, file size, method, blender version
    """
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="blender_render_"
    ) as f:
        f.write(bpy_script_content)
        script_path = f.name

    try:
        return render_script_file(script_path, output_path, timeout=timeout)
    finally:
        os.unlink(script_path)
