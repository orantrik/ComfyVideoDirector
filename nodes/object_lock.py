"""Force any prompt to interact only with objects already present in the scene.

Drop this between your prompt text and the CLIPTextEncode: it appends strict
object-permanence instructions so the model uses existing props (e.g. the glass
already on the table) instead of spawning new ones. Optionally give it a list of
the actual objects in the scene (typed, or wired from a Qwen3-VL inventory) to
anchor even harder.
"""

POSITIVE_LOCK = (
    " The scene already contains every object needed. The subject interacts ONLY "
    "with objects that are already physically present and visible in the frame, "
    "and any object a person holds must be one that was already in the scene "
    "(for example, they pick up the existing glass from the table rather than a "
    "new one). Maintain strict object permanence: objects keep their identity, "
    "material, position and count, and nothing new appears."
)

OBJECT_NEG = (
    "new objects appearing, props materializing in hands, objects spawning from "
    "nowhere, duplicated objects, extra items, object teleporting into frame, "
    "inconsistent props, objects changing or multiplying, hands suddenly holding "
    "items that were not present in the scene, floating objects"
)


class AIDirectorObjectLock:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"default": "", "multiline": True,
                                      "placeholder": "Your prompt (type here, or convert to input to wire one in)."}),
                "strictness": (["standard", "strict"], {"default": "strict"}),
            },
            "optional": {
                # Optional inventory of what's actually in the scene. Type it, or
                # wire a Qwen3-VL object-list output here for auto-anchoring.
                "scene_objects": ("STRING", {"default": "", "multiline": True,
                                             "placeholder": "Optional: glass of water on the table, laptop, phone..."}),
                "existing_negative": ("STRING", {"default": "", "multiline": True,
                                                 "placeholder": "Optional: your current negative prompt; object terms get appended."}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "negative")
    FUNCTION = "amend"
    CATEGORY = "AI Director/Prompts"

    def amend(self, prompt, strictness, scene_objects="", existing_negative=""):
        base = (prompt or "").rstrip()

        inventory = ""
        if scene_objects and scene_objects.strip():
            inventory = (
                " The only interactive objects present in the scene are: "
                f"{scene_objects.strip().rstrip('.')}. The subject may use only "
                "these and must not introduce any other object."
            )

        lock = POSITIVE_LOCK
        if strictness == "strict":
            lock += (" Do not add, create, generate or imagine any object, prop, "
                     "food, drink, tool or item that is not already visible.")

        amended = (base + inventory + lock).strip()

        neg = OBJECT_NEG
        if existing_negative and existing_negative.strip():
            neg = existing_negative.strip().rstrip(",") + ", " + OBJECT_NEG

        return (amended, neg)
