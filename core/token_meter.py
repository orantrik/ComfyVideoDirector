"""Token and dollar-cost meter for Gemini vision + NanoBanana image-gen calls.

Every paid API call goes through ComfyUI nodes, so we cannot read token counts
directly from the HTTP response. Instead we estimate:

  input tokens  = (prompt text chars / 4) + (image tokens per image)
  output tokens = (response text chars / 4)
  image cost    = fixed per-image price for the active NanoBanana model

Pricing (June 2026, USD) — update VISION_PRICING / IMAGE_PRICING if rates change.
  Gemini 3.1 Pro:        $2.00 / 1M input   $12.00 / 1M output
  Gemini 3 Flash:        $0.50 / 1M input   $ 3.00 / 1M output
  Gemini 2.5 Pro:        $1.25 / 1M input   $10.00 / 1M output
  Gemini 2.5 Flash:      $0.30 / 1M input   $ 2.50 / 1M output
  NanoBanana (3.1 Flash Image): ~$0.075/image  (range $0.045–$0.151)
  NanoBanana (2.5 Flash Image): ~$0.039/image

The meter is a module-level singleton reset at the start of each pipeline run.
The pipeline prints a [tokens] line after each scene so the GUI can parse it.
"""

# ── Pricing tables (USD) ─────────────────────────────────────────────────── #

VISION_PRICING = {
    # model-id fragment (lowercase) -> ($/1M input, $/1M output)
    "gemini-3-1-pro":   (2.00, 12.00),
    "gemini-3.1-pro":   (2.00, 12.00),
    "gemini-3-1-flash": (0.50,  3.00),
    "gemini-3.1-flash": (0.50,  3.00),
    "gemini-3-flash":   (0.50,  3.00),
    "gemini-3.flash":   (0.50,  3.00),
    "gemini-2.5-pro":   (1.25, 10.00),
    "gemini-2.5-flash": (0.30,  2.50),
    "gemini-2.5-lite":  (0.10,  0.40),
    "default":          (2.00, 12.00),  # assume Pro if unrecognised
}

IMAGE_PRICING = {
    # NanoBanana model name fragment (lowercase) -> $/image
    "3.1 flash image":  0.075,
    "flash image":      0.075,
    "2.5 flash image":  0.039,
    "nano banana 2":    0.075,
    "default":          0.075,
}

# Nano Banana Pro (gemini-3-pro-image) priced by output resolution.
PRO_IMAGE_PRICING = {
    "1k": 0.134,
    "2k": 0.134,
    "4k": 0.240,
    "default": 0.134,
}

# Tokens charged per image uploaded to a Gemini vision call (MEDIUM resolution,
# Gemini 3 series).  Gemini 3 MEDIUM = 560 tokens/image.
IMAGE_INPUT_TOKENS = 560


class TokenMeter:
    """Accumulates token/cost estimates for a single pipeline run."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.input_tokens  = 0
        self.output_tokens = 0
        self.image_calls   = 0      # NanoBanana Flash / image-gen credits
        self.pro_image_calls = 0    # Nano Banana Pro (gemini-3-pro-image) credits
        self.tts_calls     = 0      # Kokoro/F5 are local — counted for info only
        self._vision_model = "gemini-3-1-pro"
        self._image_model  = "Nano Banana 2 (Gemini 3.1 Flash Image)"
        self._pro_res      = "2k"

    # -- configuration ------------------------------------------------------- #

    def set_vision_model(self, model_id: str):
        self._vision_model = (model_id or "").strip()

    def set_image_model(self, model_name: str):
        self._image_model = (model_name or "").strip()

    def set_pro_resolution(self, resolution: str):
        self._pro_res = (resolution or "2k").strip().lower()

    # -- recording ----------------------------------------------------------- #

    def add_analyze(self, prompt_text: str, response_text: str):
        """Record one Gemini vision analysis call."""
        # +1 image worth of tokens for the uploaded screenshot/reference
        self.input_tokens  += max(1, len(prompt_text or "")  // 4) + IMAGE_INPUT_TOKENS
        self.output_tokens += max(1, len(response_text or "") // 4)

    def add_image(self, pro=False):
        """Record one image-generation credit (Flash by default, or Pro)."""
        if pro:
            self.pro_image_calls += 1
        else:
            self.image_calls += 1

    def add_tts(self):
        """Record one TTS call (Kokoro / F5 — local, no cost)."""
        self.tts_calls += 1

    # -- cost calculation ---------------------------------------------------- #

    def _vision_rates(self):
        key = self._vision_model.lower().replace("_", "-")
        for frag, rates in VISION_PRICING.items():
            if frag in key:
                return rates
        return VISION_PRICING["default"]

    def _image_rate(self):
        key = self._image_model.lower()
        for frag, rate in IMAGE_PRICING.items():
            if frag != "default" and frag in key:
                return rate
        return IMAGE_PRICING["default"]

    def vision_cost(self) -> float:
        in_rate, out_rate = self._vision_rates()
        return (self.input_tokens * in_rate + self.output_tokens * out_rate) / 1_000_000

    def _pro_rate(self) -> float:
        return PRO_IMAGE_PRICING.get(self._pro_res, PRO_IMAGE_PRICING["default"])

    def image_cost(self) -> float:
        return (self.image_calls * self._image_rate()
                + self.pro_image_calls * self._pro_rate())

    def total_cost(self) -> float:
        return self.vision_cost() + self.image_cost()

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    # -- reporting ----------------------------------------------------------- #

    def log_line(self) -> str:
        """One-line summary for the pipeline log; GUI parses lines starting [tokens]."""
        return (
            f"[tokens] "
            f"in={self.input_tokens:,}  out={self.output_tokens:,}  "
            f"imgs={self.image_calls}  pro={self.pro_image_calls}  "
            f"tts={self.tts_calls}  |  "
            f"~{self.total_tokens():,} tokens  /  ~${self.total_cost():.4f}"
        )

    def summary(self) -> str:
        """Multi-line end-of-run summary."""
        in_r, out_r = self._vision_rates()
        ir = self._image_rate()
        pr = self._pro_rate()
        flash_cost = self.image_calls * ir
        pro_cost = self.pro_image_calls * pr
        return (
            f"[cost summary]\n"
            f"  Vision   : {self.input_tokens:,} input + {self.output_tokens:,} output tokens"
            f"  (${self.vision_cost():.4f})  [${in_r}/1M in  ${out_r}/1M out]\n"
            f"  Images   : {self.image_calls} Flash x ${ir:.3f} = ${flash_cost:.4f}\n"
            f"  Pro imgs : {self.pro_image_calls} Pro({self._pro_res.upper()}) "
            f"x ${pr:.3f} = ${pro_cost:.4f}\n"
            f"  TTS      : {self.tts_calls} calls (local, free)\n"
            f"  TOTAL    : ~${self.total_cost():.4f} USD (estimates only)"
        )


# Module-level singleton — replaced each run by archviz_director.main()
METER = TokenMeter()
