# Stage 2 run records

This table is operational evidence, not recognition accuracy.

| Probe | Image | Status | Duration ms | Peak allocated MiB | Reason | Record |
|---|---|---|---:|---:|---|---|
| micro_sam | realpart_img1 | ok | 5626.687 | 2535.171 | completed | [metrics](micro_sam/realpart_img1/metrics.json) |
| micro_sam | realpart_img2 | ok | 3068.789 | 2535.901 | completed | [metrics](micro_sam/realpart_img2/metrics.json) |
| dino | realpart_img1 | ok | 2213.407 | 145.933 | Feature extraction completed; full three-class separability unavailable because draft classes are missing. | [metrics](dino/realpart_img1/metrics.json) |
| dino | realpart_img2 | ok | 1957.814 | 145.933 | Feature extraction completed; full three-class separability unavailable because draft classes are missing. | [metrics](dino/realpart_img2/metrics.json) |
| patchcore | realpart_img1 | blocked | n/a | n/a | No gold-standard clean reference photographs of the same design; real inference is blocked. | [metrics](patchcore/realpart_img1/metrics.json) |
| patchcore | realpart_img2 | blocked | n/a | n/a | No gold-standard clean reference photographs of the same design; real inference is blocked. | [metrics](patchcore/realpart_img2/metrics.json) |
| patchcore | synthetic_normal | ok | 1708.016 | 123.498 | completed | [metrics](patchcore/synthetic_normal/metrics.json) |
| patchcore | synthetic_foreign | ok | 94.222 | 123.498 | completed | [metrics](patchcore/synthetic_foreign/metrics.json) |
