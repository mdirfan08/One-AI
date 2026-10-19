"""Capability and task-kind registry.

A task kind declares which provider capabilities it needs and which artifact types
it is allowed to promise. Providers declare capabilities they actually implement.
A task can therefore never claim an output (e.g. a finished video) that no
implemented capability can produce.
"""
from __future__ import annotations

from dataclasses import dataclass

TEXT = "text"
JSON = "json"
CODE = "code"
IMAGE_GEN = "image_generation"
VIDEO_GEN = "video_generation"
TTS = "speech_synthesis"
WEB_SEARCH = "web_search"

ALL_CAPABILITIES = (TEXT, JSON, CODE, IMAGE_GEN, VIDEO_GEN, TTS, WEB_SEARCH)

ARTIFACT_TYPES = ("markdown", "json", "code_bundle", "image", "pptx", "docx", "video", "audio", "manual_note", "text")


@dataclass(frozen=True)
class KindSpec:
    label: str
    requires: frozenset
    outputs: frozenset          # artifact types a task of this kind may declare
    default_out_tokens: int
    description: str
    consequential: bool = False  # real-money / hard-to-undo: default to requiring approval


KINDS = {
    "research": KindSpec("Research", frozenset({TEXT}), frozenset({"markdown"}), 4096,
                         "Model-generated research notes. Not verified against live sources."),
    "analysis": KindSpec("Analysis", frozenset({TEXT}), frozenset({"markdown", "json"}), 4096, "Analysis of provided material."),
    "writing": KindSpec("Writing", frozenset({TEXT}), frozenset({"markdown"}), 4096, "Marketing copy, articles, website content."),
    "social": KindSpec("Social media", frozenset({TEXT}), frozenset({"markdown"}), 2048, "Social media posts and calendars."),
    "document": KindSpec("Document", frozenset({TEXT}), frozenset({"markdown", "docx"}), 6000, "Structured document (Markdown, exported to .docx)."),
    "presentation": KindSpec("Presentation", frozenset({TEXT, JSON}), frozenset({"json", "pptx"}), 6000, "Slide outline exported to .pptx."),
    "code_spec": KindSpec("Technical spec", frozenset({TEXT}), frozenset({"markdown"}), 4096, "Technical specification, file tree, plan."),
    "code_files": KindSpec("Code generation", frozenset({TEXT, CODE}), frozenset({"code_bundle"}), 12000, "Source files written to a controlled workspace."),
    "code_review": KindSpec("Code review", frozenset({TEXT, CODE}), frozenset({"markdown"}), 4096, "Review of generated code."),
    "image": KindSpec("Image generation", frozenset({IMAGE_GEN}), frozenset({"image"}), 0, "Image via a provider image API.", consequential=True),
    "video_script": KindSpec("Video script", frozenset({TEXT}), frozenset({"markdown"}), 3000, "Script only; not a video."),
    "video_storyboard": KindSpec("Video storyboard", frozenset({TEXT}), frozenset({"markdown"}), 4000, "Shot list / storyboard text; not a video."),
    "video_assets": KindSpec("Video asset generation", frozenset({VIDEO_GEN}), frozenset({"video", "image", "manual_note"}), 0,
                             "Clips/footage. No built-in adapter implements this: manual handoff.", consequential=True),
    "voiceover": KindSpec("Voiceover", frozenset({TTS}), frozenset({"audio", "manual_note"}), 0,
                          "Speech synthesis. No built-in adapter implements this: manual handoff.", consequential=True),
    "video_assembly": KindSpec("Video assembly plan", frozenset({TEXT}), frozenset({"markdown"}), 3000,
                               "Written assembly/editing plan. This app does not render video."),
    "general": KindSpec("General", frozenset({TEXT}), frozenset({"markdown"}), 4096, "General-purpose assistance."),
}
