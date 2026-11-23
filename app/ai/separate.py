# SPDX-License-Identifier: GPL-3.0-or-later
"""Instrument separation with Demucs (htdemucs_6s: drums, bass, other, vocals, guitar, piano), in one pass."""
import numpy as np


def device_name():
    import torch
    return 'cuda' if torch.cuda.is_available() else 'cpu'


def separate(audio, sr, model_name='htdemucs_6s', progress=None, log=print):
    """audio: float32 [n, channels] at `sr`. Returns ({source: float32 [n, 2]}, model sample rate)."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    from scipy.signal import resample_poly

    torch.set_num_threads(max(1, (__import__('os').cpu_count() or 2)))
    model = get_model(model_name)
    model.eval()
    msr = int(model.samplerate)
    x = audio if audio.ndim == 2 else audio[:, None]
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    x = x[:, :2].astype(np.float32)
    if sr != msr:
        from math import gcd
        g = gcd(int(sr), msr)
        x = resample_poly(x, msr // g, int(sr) // g, axis=0).astype(np.float32)
    wav = torch.from_numpy(np.ascontiguousarray(x.T))
    ref = wav.mean(0)
    mean, std = float(ref.mean()), float(ref.std()) or 1.0
    wav = (wav - mean) / std
    n = wav.shape[-1]
    nmodels = len(getattr(model, 'models', [model]))
    done = [0.0]

    def cb(d):
        if d.get('state') != 'end':
            return
        frac = (d.get('model_idx_in_bag', 0) + min(1.0, (d.get('segment_offset', 0) + 0.0) / max(1, n))) / max(1, nmodels)
        if frac > done[0]:
            done[0] = frac
            if progress:
                progress(frac)

    dev = device_name()
    log(f'separating with {model_name} on {dev} ({n / msr:.0f} s of audio)')
    with torch.no_grad():
        out = apply_model(model, wav[None], device=dev, shifts=1, split=True, overlap=0.25, progress=False,
                          num_workers=0, callback=cb)[0]
    out = out * std + mean
    stems = {name: out[i].cpu().numpy().T.astype(np.float32) for i, name in enumerate(model.sources)}
    return stems, msr
