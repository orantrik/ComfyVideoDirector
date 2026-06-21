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
    "Look at this image. For EVERY visible element (each major built/structural "
    "element or furniture piece, each object, and any people), return strict JSON: "
    'a list of {"id","label","kind","x","y","area"}. '
    "x and y are the element's CENTRE expressed as FRACTIONS of the image width and "
    "height \u2014 each a decimal between 0.0 and 1.0 (left edge x=0.0, right edge x=1.0, "
    "top y=0.0, bottom y=1.0; e.g. dead centre = 0.5,0.5). Do NOT output pixel "
    "values. 'kind' is one of furniture, object or person. 'area' is the zone it "
    "sits in. Output ONLY the JSON array, nothing else."
)

# --------------------------------------------------------------------------- #
#  Stage C/E - NanoBanana generation
# --------------------------------------------------------------------------- #
GEN_PACKSHOT_4VIEW = (
    "The reference image is a real photograph of the room that contains this item. "
    "Find the item described below in that photo and create a studio product packshot "
    "of THAT EXACT item (same material, colour, shape and proportions as seen in the "
    "photo) on a pure white seamless background, soft even studio lighting, no props, "
    "no shadows on the subject. Show it from FOUR perspectives in a 2x2 grid: front, "
    "side, three-quarter, and back. Do not redesign or stylise it. Item: {desc}. "
    "Photorealistic, sharp, catalogue quality."
)

GEN_SPACE_MAP = (
    "The FIRST reference image is a real photograph of this exact scene and is the "
    "absolute ground truth. Produce a top-down / isometric annotated map of THIS "
    "EXACT scene: reproduce the real layout, proportions and the position of every "
    "element (buildings/structures or furniture, openings, site features) exactly as "
    "seen in the photo. Do NOT invent, move, add or remove anything. Add clear text "
    "LABELS and coloured SEGMENTATION overlays for each distinct zone and major "
    "element group. Cross-check against this description (the photo wins on any "
    "conflict): {desc}."
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
    "The FIRST reference image is a real photograph of this scene and is the "
    "ABSOLUTE GROUND TRUTH for architecture, camera angle, proportions, massing, "
    "openings, ground/floor and the position of every element. Reproduce that EXACT "
    "space photorealistically. Use the additional reference images only as the "
    "identity of individual elements. Do NOT add, remove, move, resize, restyle or "
    "invent any building, structure, furniture, object, landscaping or architectural "
    "feature \u2014 keep the space identical to the photo. Natural architectural lighting. "
    "Space description (cross-check only, the photo wins on any conflict): {space}."
)

GEN_HERO_COMPOSITE = (
    "The FIRST reference image is a real photograph of this EXACT scene and is the "
    "absolute ground truth for the space: keep its architecture, camera angle, "
    "proportions, massing, openings, lighting, ground/floor and the position of every "
    "element IDENTICAL to the photo. Do not alter, move, restyle or invent any part of "
    "the space. Using the other reference images for the identity of each person and "
    "element, place the spokesman and the secondary actors naturally INTO this same "
    "space according to these positions: {coords}. Every person matches their reference "
    "sheet exactly (face, hair, outfit). People only interact with elements that already "
    "exist in the scene. Do not invent any new object, prop, person, building or "
    "architectural feature. Photorealistic, cinematic, fully consistent with the "
    "reference photo. Scene description (cross-check only, the photo wins): {space}."
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


# --------------------------------------------------------------------------- #
#  EXTERIOR ArchViz variants
#  Selected at runtime when --scene-type exterior is passed. These reuse the
#  same code paths (the orchestrator keeps the "furniture"/"objects" buckets)
#  but ask Qwen/NanoBanana about buildings, site features and outdoor props
#  instead of interior furnishings.
# --------------------------------------------------------------------------- #
ANALYSIS_ELEMENTS_EXT = (
    "You are an architectural visualization supervisor analysing an EXTERIOR scene. "
    "List every major BUILT or STRUCTURAL element: each building/tower, podium, "
    "annex, roof structure, façade system, balcony band, entrance/canopy, bridge, "
    "pergola, retaining wall and major hardscape structure. For each, output one "
    "line:\n"
    "id | short name | form, materials, colour, key façade features | exact location "
    "in the scene (left/right/centre, foreground/background, relative to other "
    "masses). Be literal and precise. Do not invent anything that is not visible."
)

ANALYSIS_SITE_OBJECTS_EXT = (
    "You are an architectural visualization supervisor analysing an EXTERIOR scene. "
    "List every secondary SITE element / outdoor object: vehicles, trees and "
    "planting, street furniture, signage, lamp posts / lighting, sculptures, "
    "bollards, fences/railings, pools and water features, awnings, and any people. "
    "For each, output one line:\n"
    "id | short name | material / colour / type | exact location (which area, near "
    "which building). Be literal. Do not invent objects that are not visible."
)

ANALYSIS_SPACE_EXT = (
    "You are an architectural visualization site analyst. Describe this EXTERIOR "
    "scene: overall site composition and style, the massing and approximate scale of "
    "each building (storeys / height, best estimate), the camera viewpoint, and a "
    "breakdown of distinct ZONES (e.g. tower cluster, podium, forecourt / parking, "
    "landscaped areas, street / approach, skyline / background) with their "
    "approximate location. Note ground surfaces, sky / time-of-day and any site "
    "caveats. Return concise structured text."
)

GEN_PACKSHOT_EXT = (
    "The reference image is a real photograph of an architectural scene. Find the "
    "element described below in that photo and create a clean isolated REFERENCE "
    "render of THAT EXACT element (same form, materials, colour, proportions and "
    "façade / detailing as seen in the photo) on a neutral plain background with even "
    "lighting. Show it from THREE to FOUR representative angles in a grid. Do not "
    "redesign, restyle, simplify or embellish it. Element: {desc}. Photorealistic, "
    "sharp, reference quality."
)

MASTER_PROMPT_EXT = (
    "Write a single cinematic master prompt describing the START FRAME of the next "
    "shot of this EXTERIOR architectural scene and how it should look and feel. Base "
    "it strictly on the locked identities below; reference the real buildings, site "
    "elements and any people by their description and location. Keep it photorealistic "
    "and continuous with the current shot.\n\nSITE:\n{space}\n\nBUILT ELEMENTS:\n"
    "{furniture}\n\nSITE OBJECTS:\n{objects}\n\nCAST:\n{cast}\n"
)


INSPECTOR_HOLISTIC = (
    "You are a QA inspector for an architectural composite image. Compare the "
    "GENERATED image against the locked INVENTORY of elements that should appear "
    "(each with a short description). Give ONE overall fidelity score from 0 to 100 "
    "for how faithfully the generated image preserves the real scene and the listed "
    "identities (100 = perfect, 0 = unrelated/hallucinated). Also list obvious "
    "hallucinations \u2014 prominent objects or people that appear but are NOT in the "
    "inventory. Return strict JSON only: "
    '{{"score": <int>, "hallucinations": [...], "notes": "..."}}\n\n'
    "INVENTORY:\n{inventory}\n"
)


def apply_scene_type(scene_type):
    """Repoint the active analysis/packshot/master prompts for the given scene type.

    Stages read the module-level P.* attributes at call time, so swapping them
    here switches the whole pipeline between interior furnishings and exterior
    buildings/site features. Idempotent and safe to call repeatedly.
    """
    g = globals()
    if str(scene_type).lower() == "exterior":
        g["ANALYSIS_FURNITURE"] = ANALYSIS_ELEMENTS_EXT
        g["ANALYSIS_OBJECTS"]   = ANALYSIS_SITE_OBJECTS_EXT
        g["ANALYSIS_SPACE"]     = ANALYSIS_SPACE_EXT
        g["GEN_PACKSHOT_4VIEW"] = GEN_PACKSHOT_EXT
        g["MASTER_PROMPT"]      = MASTER_PROMPT_EXT
    return scene_type


def fill(template, **kw):
    return template.format(**kw)
