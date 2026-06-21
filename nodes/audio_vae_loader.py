"""Safe VAE loader that bypasses the desktop's comfy-aimdo lazy model loader.

WHY THIS EXISTS
---------------
ComfyUI Desktop ships `comfy-aimdo`, an accelerated loader that monkeypatches
`comfy.utils.load_torch_file` to return a lazy memory-mapped `ModelMMAP` object
instead of a normal state-dict.

That lazy object works for most models because PyTorch's `load_state_dict`
streams it. But loading the **LTX audio VAE** makes `comfy.sd.VAE.__init__`
run `state_dict_prefix_replace(sd, {"audio_vae.": "autoencoder."})`, which
iterates / rebuilds the whole dict. On the lazy `ModelMMAP` that iteration calls
`get_file_handle`, which doesn't exist -> hard crash:

    VAELoader (node 343): 'ModelMMAP' object has no attribute 'get_file_handle'

Both the core `VAELoader` and KJNodes `VAELoaderKJ` hit this, because both route
through the patched `comfy.utils.load_torch_file`.

THE FIX
-------
Read the .safetensors file directly with the `safetensors` library (a real,
fully-materialized dict — never touches comfy-aimdo), then hand that plain dict
to `comfy.sd.VAE`. The VAE constructor's prefix-rewrite now runs on a normal
dict and succeeds. Used for the audio VAE in the LTX lip-sync recipe.
"""

import comfy.sd
import folder_paths


def _load_state_dict_real(path):
    """Load a .safetensors file into a real dict (bypassing comfy-aimdo)."""
    from safetensors import safe_open
    sd = {}
    metadata = {}
    with safe_open(path, framework="pt", device="cpu") as f:
        try:
            metadata = f.metadata() or {}
        except Exception:
            metadata = {}
        for key in f.keys():
            sd[key] = f.get_tensor(key)
    return sd, metadata


class AIDirectorVAELoaderSafe:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"vae_name": (folder_paths.get_filename_list("vae"),)}}

    RETURN_TYPES = ("VAE",)
    FUNCTION = "load_vae"
    CATEGORY = "AI Director/Loaders"
    DESCRIPTION = ("Loads a VAE (incl. the LTX audio VAE) directly via safetensors, "
                   "bypassing the comfy-aimdo lazy loader that crashes on the "
                   "audio-VAE prefix rewrite.")

    def load_vae(self, vae_name):
        path = folder_paths.get_full_path_or_raise("vae", vae_name)
        sd, metadata = _load_state_dict_real(path)
        vae = comfy.sd.VAE(sd=sd, metadata=metadata or None)
        vae.throw_exception_if_invalid()
        return (vae,)


NODE_CLASS_MAPPINGS = {"AIDirectorVAELoaderSafe": AIDirectorVAELoaderSafe}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AIDirectorVAELoaderSafe": "AI Director - Safe VAE Loader (audio/aimdo-proof)"
}
