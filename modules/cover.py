from __future__ import annotations

from pathlib import Path
from statistics import mean, pstdev

from PIL import Image

from modules.discovery import BookProject


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def analyze_cover(path: Path) -> dict:
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        thumb = rgb.resize((100, max(1, round(100 * height / width))))
        pixels = list(thumb.getdata())
        lum = [_luminance(pixel) for pixel in pixels]
        return {
            "path": str(path),
            "width": width,
            "height": height,
            "aspect_ratio_height_width": round(height / width, 3),
            "mode": img.mode,
            "file_size_bytes": path.stat().st_size,
            "thumbnail_width": thumb.size[0],
            "thumbnail_height": thumb.size[1],
            "average_luminance": round(mean(lum), 2),
            "luminance_stddev": round(pstdev(lum), 2),
            "very_light_cover": mean(lum) > 220,
            "low_thumbnail_contrast_risk": pstdev(lum) < 35,
        }


def render_cover_review(project: BookProject) -> str:
    if not project.cover:
        return "# Cover Review\n\nNo cover image was found. Status: FIX."

    data = analyze_cover(project.cover)
    ratio = data["aspect_ratio_height_width"]
    ratio_note = "OK" if 1.45 <= ratio <= 1.65 else "REVIEW"
    resolution_note = "OK" if data["height"] >= 2500 and data["width"] >= 1500 else "REVIEW"
    contrast_note = "REVIEW" if data["low_thumbnail_contrast_risk"] else "OK"
    edge_note = "REVIEW" if data["very_light_cover"] else "OK"

    recommendations: list[str] = []
    if resolution_note != "OK":
        recommendations.append("Export the Kindle cover at a higher production size before upload; target at least 1600 x 2560 px.")
    if ratio_note != "OK":
        recommendations.append("Check KDP cover aspect ratio. Kindle covers usually perform best near 1.6 height/width.")
    if contrast_note != "OK":
        recommendations.append("Increase title/number contrast for 100px Amazon thumbnail readability.")
    if edge_note != "OK":
        recommendations.append("Use a very subtle border or edge treatment so the cover does not disappear on Amazon white backgrounds.")
    if not recommendations:
        recommendations.append("Keep the design restrained. Do not add AI imagery, robots, neon, or extra symbols.")

    return "\n".join([
        "# Cover Review",
        "",
        f"Project: `{project.project_id}`",
        f"Cover: `{project.cover}`",
        "",
        "## Technical Check",
        "",
        f"- Size: {data['width']} x {data['height']} px",
        f"- Resolution readiness: {resolution_note}",
        f"- Height/width ratio: {ratio} ({ratio_note})",
        f"- File size: {data['file_size_bytes']} bytes",
        f"- 100px thumbnail simulation: {data['thumbnail_width']} x {data['thumbnail_height']} px",
        f"- Thumbnail contrast risk: {contrast_note}",
        f"- Light-background edge risk: {edge_note}",
        "",
        "## Publishing Assessment",
        "",
        "- Thumbnail readability: verify title and core number remain readable at 100px.",
        "- Premium feel: preserve negative space and editorial restraint.",
        "- AI-slop risk: avoid AI imagery, robots, neon, generic tech patterns, and decorative gradients.",
        "- Title hierarchy: the strongest commercial hook must dominate.",
        "- Subtitle readability: acceptable if readable at product-page size; not required at tiny thumbnail.",
        "- Author name placement: keep restrained.",
        "- Business nonfiction fit: serious, cold, credible, not startup-aesthetic.",
        "- Production fit: verify that the uploaded paperback/PDF cover, if separate, includes correct trim size, bleed, spine width, and barcode area.",
        "- Storefront fit: inspect the cover on white, light gray, and mobile dark-mode contexts before final upload.",
        "",
        "## Exact Recommendations",
        "",
        *(f"- {item}" for item in recommendations),
    ])
