# DUNNIO ChatGPT Project Instructions

This Project contains two mutually exclusive image-generation workflows.

When a message begins with `TASK=TEXTURE`:

- Use only `01_TEXTURE_SEAMLESS_MASTER.md` as the generation specification.
- Ignore `02_FABRIC_SWATCH_MASTER.md` completely.
- Treat the source image newly uploaded with that message as authoritative.
- Generate exactly one final seamless master texture and no explanation.

When a message begins with `TASK=FABRIC_SWATCH`:

- Use only `02_FABRIC_SWATCH_MASTER.md` as the generation specification.
- Ignore `01_TEXTURE_SEAMLESS_MASTER.md` completely.
- Treat the texture image newly uploaded with that message as authoritative.
- Generate exactly one final 4:3 landscape fabric image and no explanation.

Never combine requirements from the two master documents. If `TASK` is missing or invalid, do not generate an image.
