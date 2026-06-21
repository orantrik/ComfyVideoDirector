"""Prompt templates for the ArchViz identity-lock pipeline.

Two families:
  * ANALYSIS_* -> fed to Qwen3-VL (vision) to describe / score the scene.
  * GEN_*      -> fed to NanoBanana (Gemini) to generate packshots / sheets / heroes.
Plus master-prompt, reconciliation and inspector templates.
"""

# --------------------------------------------------------------------------- #
#  Stage B - Qwen3-VL analysis of the source screenshot
# --------------------------------------------------------------------------- #
ANALYSIS_FURNITURE = (
    "You are an ArchViz set supervisor. Look at this room screenshot and list "
    "every distinct piece of FURNITURE (sofas, chairs, tables, beds, cabinets, "
    "shelving, lighting fixtures). For each, output one line:\n"
    "id | short name | material & colour & style | exact location in the room "
    "(wall/corner/centre, left/right, near which other item). Be literal and "
    "precise. Do not invent furniture that is not visible."
)

ANALYSIS_OBJECTS = (
    "You are an ArchViz set supervisor. Look at this room screenshot and list "
    "every small OBJECT / prop a person could interact with or that dresses the "
    "set (glasses, bottles, books, vases, cushions, artwork, plants, remotes, "
    "decor). For each, output one line:\n"
    "id | short name | material/colour | exact location (on which furniture, "
    "which side). Be literal. Do not invent objects that are not visible."
)

ANALYSIS_SPACE = (
    "You are an ArchViz spatial analyst. Describe this room as a space: overall "
    "shape and style, estimated dimensions (width x depth x height in metres, "
    "best estimate), and a breakdown of distinct AREAS/zones (e.g. lounge area, "
    "dining area, entry, window wall) with their approximate location and size. "
    "Note wall features, openings, windows and any architectural caveats. Return "
    "concise structured text."
)

ANALYSIS_COORDINATES = (
    "Look at this image. For EVERY visible element (each furniture piece, each "
    "object, and any people), return strict JSON: a list of "
    '{"id","label","kind","x","y","area"} where x,y are normalized 0..1 image '
    "coordinates of the element's centre and area is the zone it sits in. Output "
    "ONLY the JSON array, nothing else."
)

# --------------------------------------------------------------------------- #
#  Stage C/E - NanoBanana generation
# --------------------------------------------------------------------------- #
GEN_PACKSHOT_4VIEW = (
    "Studio product packshot of this exact item on a pure white seamless "
    "background, soft even studio lighting, no props, no shadows on the subject. "
    "Show it from FOUR perspectives in a 2x2 grid: front, side, three-quarter, "
    "and back. Keep the exact same object identity (material, colour, proportions) "
    "as described: {desc}. Photorealistic, sharp, catalogue quality."
)

GEN_SPACE_MAP = (
    "Top-down / isometric annotated map of this room. Keep the real layout. Add "
    "clear text LABELS and coloured SEGMENTATION overlays for each distinct area "
    "and major furniture group, so each zone is identifiable. Based on: {desc}."
)

GEN_CHARACTER_SHEET = (
    "Character reference sheet of the SAME person, identical face, hair, body and "
    "outfit in every view, on a neutral light-grey studio background. Show {n} "
    "evenly-rotated full-body angles in a grid (front, three-quarters, profiles, "
    "back). Consistent lighting. The person: {desc}. Photorealistic, sharp."
)

GEN_PORTRAIT = (
    "Photorealistic close-up portrait of this exact person, identical face and "
    "hair, neutral studio lighting, shallow depth of field. The person: {desc}."
)

GEN_CLOTHES_PACKSHOT = (
    "Studio packshot of this exact garment on a pure white background, no person, "
    "flat-lay or mannequin, four angles in a 2x2 grid. The garment: {desc}."
)

GEN_EMPTY_SPACE_STABILIZED = (
    "A clean, photorealistic wide shot of this empty room with its furniture and "
    "objects exactly where they are, matching the reference layout and the "
    "provided packshots for every item. Do not add, remove, move or invent any "
    "furniture or object. Natural architectural lighting. Space: {space}."
)

GEN_HERO_COMPOSITE = (
    "Photorealistic wide shot of this room, fully dressed, with the spokesman and "
    "the secondary actors placed naturally according to these positions: {coords}. "
    "Every person matches their reference sheet exactly (face, hair, outfit). "
    "Every furniture piece and object matches its packshot and stays in its real "
    "location. People only interact with objects that already exist in the room. "
    "Do not invent any new object, prop or person. Natural lighting, cinematic, "
    "photorealistic. Scene: {space}."
)

# --------------------------------------------------------------------------- #
#  Stage G - master prompt (start frame of NEXT scene)
# --------------------------------------------------------------------------- #
MASTER_PROMPT = (
    "Write a single cinematic master prompt describing the START FRAME of the "
    "next scene and how the space should look and feel. Base it strictly on the "
    "locked identities below; reference real furniture, objects and people by "
    "their description and location. Keep it photorealistic and continuous with "
    "the current scene.\n\nSPACE:\n{space}\n\nFURNITURE:\n{furniture}\n\n"
    "OBJECTS:\n{objects}\n\nCAST:\n{cast}\n"
)

# --------------------------------------------------------------------------- #
#  Stage J - prompt reconciliation (bind actions to existing elements)
# --------------------------------------------------------------------------- #
RECONCILE = (
    "You are a continuity supervisor. Rewrite the ACTION PROMPT below so that "
    "EVERY action references an element that actually exists in this scene, by "
    "its identity and location. For example, 'he drinks' becomes 'he lifts the "
    "<exact glass> from the <exact table/location> and drinks'. Never introduce "
    "an object, prop or person that is not in the inventory. Keep the cinematic "
    "tone. Return only the rewritten prompt.\n\n"
    "INVENTORY (furniture, objects, people with locations):\n{inventory}\n\n"
    "COORDINATES JSON:\n{coords}\n\nACTION PROMPT:\n{prompt}\n"
)

# --------------------------------------------------------------------------- #
#  Stage K - inspector (identity match scoring)
# --------------------------------------------------------------------------- #
INSPECTOR = (
    "You are a QA inspector. Compare the GENERATED image against the locked "
    "reference for '{label}' (description: {desc}). Score how faithfully the "
    "generated element matches the reference identity from 0 to 100, where 100 is "
    "identical and 0 is unrelated/hallucinated. Also flag if any object or person "
    "appears that is NOT in the locked inventory. Return strict JSON: "
    '{{"label":"{label}","score":<int>,"hallucinations":[...],"notes":"..."}}'
)


def fill(template, **kw):
    return template.format(**kw)
