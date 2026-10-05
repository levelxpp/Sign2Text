# Model Results

Every figure below is traceable to a specific artifact. Where a number could
not be verified against a stored artifact, it is listed as unverified and is
**not** used anywhere else in this repository.

**Attribution.** The ML pipeline and training-framework code were written by
Stepan Karapetyan. The training runs, experiments, training monitoring,
checkpoint comparison and selection, evaluation, and the verification of every
result recorded below were carried out by Koko Jamgotchian. See
[`MY_CONTRIBUTIONS.md`](MY_CONTRIBUTIONS.md) and
[`THIRD_PARTY_ATTRIBUTION.md`](THIRD_PARTY_ATTRIBUTION.md).

No model weights are distributed here — see
[`COPYRIGHT.md`](COPYRIGHT.md) and the README section
*"Known public-release limitations."*

---

## 1. Word-level ASL recognition — `curated600_resume2`

**The model used for the live demo.**

| | |
| --- | --- |
| Architecture | CTR-GCN + Conformer |
| Graph layout | `mediapipe86` (86 keypoints) |
| Input shape | `[batch, 64 frames, 86 joints, 16 channels]` |
| Vocabulary | 604-class curated set |
| Training data | ASL Citizen (curated subset) |
| Optimizer | AdamW, lr 5e-4, weight decay 0.1, label smoothing 0.1 |
| Batch size | 256, bucket sampler, AMP enabled |
| Feature engineering | Kinematics + bone features |
| Augmentation | Geometric (±15° rot, 0.8–1.2 scale), signal noise, temporal scaling, advanced spatial/temporal masking |

### Verified results

Read directly from the `metrics` dictionary stored inside
`curated600_resume2_best.ckpt` (epoch 181, step 17,836):

| Metric | Value |
| --- | --- |
| **Validation top-1 accuracy** | **92.53%** |
| **Validation top-5 accuracy** | **98.48%** |
| Validation loss | 1.2920 |
| Training top-1 accuracy | 99.49% |
| Training top-5 accuracy | 99.95% |
| Training loss | 1.0098 |
| Learning rate at best epoch | 2.225 × 10⁻⁴ |

**Validation set:** the held-out `asl_citizen_val.arrow` split of the curated
604-class vocabulary, as configured in
`backend/checkpoint/curated600_resume2/curated600_resume2.yaml`.

**Evidence:** checkpoint-embedded metrics. Directly verified.

> The ~7-point train/validation accuracy gap is consistent with the heavy
> augmentation schedule above — training accuracy is measured on augmented
> batches, validation on clean clips.

---

## 2. Word-level ASL recognition — `popsign_aslcitizen_semlex6_gcn_big`

An alternative 347-class checkpoint, selectable via `ASL_PIPELINE`.

| | |
| --- | --- |
| Architecture | CTR-GCN + Transformer |
| Model dimensions | `d_model` 256, 8 heads, 4 layers, FFN 768 |
| CTR-GCN | channels `[7, 64, 64, 7]`, 3 subsets, reduction 8 |
| Pooling | CLS token |
| Classes | 347 |
| Training data | PopSign + ASL Citizen + Sem-Lex |
| Regularization | dropout 0.3, input dropout 0.2, mixup (p=0.25), time/feature masking, EMA (decay 0.999) |

### Verified results

Read from the `best_val_acc` field inside `best_model.pt` (epoch 1,119):

| Metric | Value |
| --- | --- |
| **Validation top-1 accuracy** | **92.47%** |

Evaluated with EMA weights (`eval_with_ema: true`), 3 clips per sample.

**Evidence:** checkpoint-embedded metric. Directly verified.
Top-5 accuracy was not stored in this checkpoint and is therefore not reported.

---

## 3. Fingerspelling (Letter mode) — `MLP_256x3`

| | |
| --- | --- |
| Architecture | MLP, 3 hidden layers × 256 units, 148,760 parameters |
| Input | 42-dim MediaPipe Hands landmark coordinates |
| Classes | 24 letters (excluding J and Z, which require motion) |
| Split | Stratified 80/20, `random_state=42` → 9,966 train / 2,492 test |

### Verified results

| Model | Test accuracy |
| --- | --- |
| **`MLP_256x3`** | **99.56%** |
| `MLP_128x2` (25,112 params) | 97.83% |
| `MLP_64x2` (8,472 params) | 95.47% |
| Logistic Regression, scaled features | 96.19% |
| Logistic Regression, raw features (baseline) | 92.66% |

Macro-averaged: precision 99.6%, recall 99.5%, F1 99.6%. No letter-pair
confusion reached a count of 5.

**Evidence:** `backend/checkpoint/letter_mlp/EVAL_NOTES.md`, which records the
dataset, split, seed, and per-letter precision/recall/F1 for all 24 classes.
Directly verified.

> The LR rows are the baseline this model replaced, kept here because the
> comparison is the actual result: swapping LR for a small MLP on identical
> features and an identical split moved accuracy from 92.66% to 99.56%.

---

## 4. Fingerspelling — `MLP_256x3_centered` (cross-signer variant)

| | |
| --- | --- |
| Preprocessing | Subtract wrist landmark, divide by wrist→middle-MCP distance, drop wrist |
| Input | 40-dim (20 landmarks × 2) |
| Split | Stratified 80/20, `random_state=42` — identical to §3 |

| Metric | Value |
| --- | --- |
| **Test accuracy** | **99.68%** (epoch 41) |

Macro-averaged: precision 99.7%, recall 99.6%, F1 99.6%.

**Evidence:** `backend/checkpoint/letter_mlp_centered/eval.md`. Directly verified.

The wrist-centering and palm-normalization make the model invariant to hand
position and scale in frame, which is what allows it to generalize across
signers and camera distances rather than only matching the training capture
setup.

---

## Comparability warning

**These results are not comparable to each other and must never be combined,
averaged, or presented as a single headline figure.**

| | Word models (§1, §2) | Letter models (§3, §4) |
| --- | --- | --- |
| Task | Isolated sign classification | Static handshape classification |
| Classes | 604 / 347 | 24 |
| Input | Video sequence, 64–128 frames | Single frame |
| Validation set | ASL Citizen held-out split | 20% split of a 12,458-row CSV |

A 92.53% top-1 over 604 dynamic sign classes and a 99.56% over 24 static
handshapes are measurements of fundamentally different problems.

---

## Unverified — deliberately excluded

An internal note recorded a `tier-1 81.8 / 94.8` figure for
`curated600_resume2`. **No supporting artifact exists** in the project: there
are no TensorBoard event files, no evaluation report, and no diagnostics
output backing it. It appears to describe a *live segment-level* evaluation on
a demo word subset — a different measurement from checkpoint validation
accuracy — but this could not be confirmed.

It is recorded here for completeness and is **not** used as a result anywhere
in this repository.

---

## Artifacts published and not published

**Published.** A TensorBoard dashboard screenshot captured during training is
included at
[`assets/images/tensorboard-training-metrics.png`](assets/images/tensorboard-training-metrics.png).
It shows training and validation Top-1 accuracy, Top-5 accuracy, and loss over a
run. TensorBoard smooths displayed curves by default, so values read off the
image are not the same as exact best-checkpoint metrics — the screenshot is
evidence that the runs were instrumented and monitored, not the source of any
number in this document.

**Not published.** The raw TensorBoard event files, confusion-matrix exports, and
per-class diagnostic CSVs were not preserved alongside the checkpoints, and the
trained weights themselves are excluded for licensing reasons. The metrics above
therefore come from values embedded in the checkpoint files and from evaluation
notes written at training time, which is the strongest evidence still available.

Training ran on separate infrastructure. The runs, monitoring, checkpoint
selection, and evaluation behind these figures were performed by Koko
Jamgotchian; the framework the runs executed on was written by Stepan
Karapetyan.
