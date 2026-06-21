from .nodes.ai_director_project import AIDirectorProjectSetup
from .nodes.frame_intake import UnrealFrameIntake
from .nodes.frame_classifier import FrameClassifier
from .nodes.path_validator import ContinuousPathValidator
from .nodes.segment_planner import SegmentPlanner
from .nodes.prompt_compiler import PromptCompiler
from .nodes.manifest_exporter import ManifestExporter
from .nodes.video_assembler import VideoAssembler
from .nodes.segment_picker import SegmentPromptPicker
from .nodes.load_frame import AIDirectorLoadFrame
from .nodes.auto_loop import AIDirectorAutoLoop, AIDirectorStoreFrame, AIDirectorStoreCaption
from .nodes.object_lock import AIDirectorObjectLock

NODE_CLASS_MAPPINGS = {
    "AIDirectorProjectSetup": AIDirectorProjectSetup,
    "UnrealFrameIntake": UnrealFrameIntake,
    "FrameClassifier": FrameClassifier,
    "ContinuousPathValidator": ContinuousPathValidator,
    "SegmentPlanner": SegmentPlanner,
    "PromptCompiler": PromptCompiler,
    "SegmentPromptPicker": SegmentPromptPicker,
    "AIDirectorLoadFrame": AIDirectorLoadFrame,
    "AIDirectorAutoLoop": AIDirectorAutoLoop,
    "AIDirectorStoreFrame": AIDirectorStoreFrame,
    "AIDirectorStoreCaption": AIDirectorStoreCaption,
    "AIDirectorObjectLock": AIDirectorObjectLock,
    "ManifestExporter": ManifestExporter,
    "VideoAssembler": VideoAssembler,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AIDirectorProjectSetup": "AI Director - Project Setup",
    "UnrealFrameIntake": "AI Director - Frame Intake",
    "FrameClassifier": "AI Director - Frame Classifier",
    "ContinuousPathValidator": "AI Director - Path Validator",
    "SegmentPlanner": "AI Director - Shot Planner",
    "PromptCompiler": "AI Director - Prompt Compiler",
    "SegmentPromptPicker": "AI Director - Segment Prompt Picker",
    "AIDirectorLoadFrame": "AI Director - Load Frame (by path)",
    "AIDirectorAutoLoop": "AI Director - Auto Loop (self-driving tour)",
    "AIDirectorStoreFrame": "AI Director - Store Last Frame (+advance)",
    "AIDirectorStoreCaption": "AI Director - Store Caption (VLM feedback)",
    "AIDirectorObjectLock": "AI Director - Use Existing Objects (anti-hallucination)",
    "ManifestExporter": "AI Director - Manifest Exporter",
    "VideoAssembler": "AI Director - Video Assembler",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
