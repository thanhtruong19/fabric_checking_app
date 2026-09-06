Transform the provided fabric scan/photo into a **production-ready seamless Base Color / Albedo texture for 3D materials**.

Use the source image as the **only authoritative reference**. Reconstruct the actual fabric; do not generate, redesign or reinterpret it.

### Preserve exactly

* base colour and colour relationships
* stripe/check/plaid geometry
* line colour, width and spacing
* pattern proportions and repeat scale
* weave structure, yarn direction/thickness and density
* herringbone/twill direction and angle
* natural fine textile detail

Do not add/remove pattern elements, recolour, rescale, sharpen, blur or simplify the textile.

### Processing

Use the cleanest authentic fabric area only. Remove labels, edges, background, objects, dust, stains and capture/scanner defects.

Correct perspective and flatten the fabric geometrically. No folds, waves or artificial depth. Structured lines must remain straight, aligned and uniformly spaced.

Create a **neutral Albedo**, removing lighting gradients, shadows, highlights, reflections, colour casts and large-scale luminance/contrast variation while preserving genuine yarn and weave detail.

Detect the **true X/Y pattern repeat** and place tile boundaries at matching pattern phases. Left↔right and top↔bottom must connect continuously with correct pattern spacing, weave, colour, luminance and contrast.

Perform a **50% X/Y offset seam test** and repair seams locally without blur, ghosting, doubled lines, stretched weave or visible cloning.

Remove identifiable random defects or bright/dark landmarks that would create obvious repetition, but never alter the deterministic textile pattern.

### Tiling requirements

The texture must remain visually stable when repeated **15×15**, with:

* no seams or tile grid
* no pattern shift or spacing errors
* no vertical/horizontal banding
* no checkerboard or repeating light/dark blocks
* no recognizable repeated defects
* no visible tile footprint or macro repetition

A texture that is seamless only at the edges but reveals its tile size when repeated is **not acceptable**.

### Output

* 2048×2048
* 1:1 square
* sRGB
* PNG lossless
* JPG ≥95 quality
* 3×3 repeat preview
* 15×15 repeat preview

Final result must look like a **perfectly flat, uniformly illuminated fabric scan used as a 3D material asset**, not a beautified fabric photograph.
