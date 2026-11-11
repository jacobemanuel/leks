"""Real models on torch: a dense embedder and a late-interaction (maxsim) reranker.

This is the only module in the engine that imports torch and transformers. It
loads from a local folder only, never runs remote code, and refuses a
checkpoint that does not fill the model exactly (random weights would be a
silent failure).
"""
from __future__ import annotations

import gc
import os

os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')
os.environ.setdefault('TRANSFORMERS_VERBOSITY', 'error')

import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import load_file
from transformers import AutoConfig, AutoModel, AutoTokenizer

from leks.backends import batch_plan, pick_device
from leks.models import ModelCheck, projection_file

DTYPES = {'float32': torch.float32}
BATCH_TOKENS = 16384        # padded tokens per batch
BATCH_TEXTS = 32            # texts per batch


def device_of(name):
    return torch.device(pick_device(name, torch.backends.mps.is_available(), torch.cuda.is_available()))


def load(path, spec, device):
    """Model and tokenizer from a local folder, strict about what was loaded."""
    if spec.dtype not in DTYPES:
        raise ModelCheck('unsupported dtype')
    cfg = AutoConfig.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    kw = dict(config=cfg, local_files_only=True, trust_remote_code=False, use_safetensors=True,
              output_loading_info=True)
    try:
        model, info = AutoModel.from_pretrained(path, dtype=DTYPES[spec.dtype], **kw)
    except TypeError:
        # older transformers call it torch_dtype
        model, info = AutoModel.from_pretrained(path, torch_dtype=DTYPES[spec.dtype], **kw)
    if info.get('missing_keys') or info.get('unexpected_keys') or info.get('mismatched_keys') or info.get('error_msgs'):
        raise ModelCheck('checkpoint does not fill the model')
    tok = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    tok.padding_side = 'right'
    tok.truncation_side = 'right'
    if tok.pad_token_id is None:
        raise ModelCheck('tokenizer has no pad token')
    return model.eval().to(device), tok


def padded(tok, enc, idx, device):
    feat = {'input_ids': [enc['input_ids'][i] for i in idx],
            'attention_mask': [enc['attention_mask'][i] for i in idx]}
    if 'token_type_ids' in enc:
        feat['token_type_ids'] = [enc['token_type_ids'][i] for i in idx]
    out = tok.pad(feat, padding=True, return_tensors='pt')
    return {k: v.to(device) for k, v in out.items()}


def finite(t):
    if not torch.isfinite(t).all():
        raise ModelCheck('non finite output')


class Enc:
    """Dense embedder: one vector per text."""

    def __init__(self, path, spec, device='auto'):
        self.dev = device_of(device)
        self.s = spec
        self.model, self.tok = load(path, spec, self.dev)

    def _pool(self, h, mask):
        p = self.s.pooling
        if p == 'cls':
            return h[:, 0]
        if p == 'mean':
            m = mask[..., None].to(h.dtype)
            return (h * m).sum(1) / m.sum(1).clamp(min=1)
        if p == 'last':
            return h[torch.arange(h.shape[0], device=h.device), mask.sum(1) - 1]
        raise ModelCheck('unknown pooling')

    @torch.inference_mode()
    def __call__(self, texts, kind='d'):
        if kind not in ('q', 'd'):
            raise ValueError('kind must be q or d')
        pre = self.s.prefixes[kind]
        enc = self.tok([pre + x for x in texts], padding=False, truncation=True,
                       max_length=self.s.max_length, return_attention_mask=True)
        out = np.zeros((len(texts), self.s.dim), dtype='float32')
        for idx in batch_plan([len(x) for x in enc['input_ids']], BATCH_TOKENS, BATCH_TEXTS):
            b = padded(self.tok, enc, idx, self.dev)
            v = self._pool(self.model(**b).last_hidden_state, b['attention_mask'])
            if self.s.normalize:
                v = F.normalize(v, p=2, dim=1)
            finite(v)
            if v.shape[1] != self.s.dim:
                raise ModelCheck('dimension differs from the config')
            out[idx] = v.float().cpu().numpy()
        return out


class MaxSim:
    """Late interaction: a vector per token through the projection stack, then
    for each query token the best document token, summed."""

    def __init__(self, path, spec, device='auto'):
        self.dev = device_of(device)
        self.s = spec
        if not spec.projection:
            raise ModelCheck('maxsim needs a projection list in the config')
        self.model, self.tok = load(path, spec, self.dev)
        self.head = []
        for d in spec.projection:
            t = load_file(projection_file(path, d['src']))
            lin = t['linear.weight'].to(torch.float32).to(self.dev)
            res = t.get('residual.weight')
            bad = (tuple(lin.shape) != (d['out'], d['in'])
                   or (res is not None) != bool(d['residual'])
                   or (res is not None and tuple(res.shape) != tuple(lin.shape))
                   or bool(set(t) - {'linear.weight', 'residual.weight'}))
            if bad:
                raise ModelCheck('projection differs from the config')
            self.head.append((lin, res.to(torch.float32).to(self.dev) if res is not None else None))
        if self.head[-1][0].shape[0] != spec.dim:
            raise ModelCheck('projection output differs from the configured dim')

    def _tokens(self, b):
        h = self.model(**b).last_hidden_state
        for lin, res in self.head:
            p = h @ lin.T
            h = (p + h @ res.T) if res is not None else p
        h = F.normalize(h, p=2, dim=-1)
        finite(h)
        return h

    @torch.inference_mode()
    def encode(self, texts, kind):
        pre = self.s.prefixes[kind]
        enc = self.tok([pre + x for x in texts], padding=False, truncation=True,
                       max_length=self.s.max_length, return_attention_mask=True)
        res = [None] * len(texts)
        for idx in batch_plan([len(x) for x in enc['input_ids']], BATCH_TOKENS, BATCH_TEXTS):
            b = padded(self.tok, enc, idx, self.dev)
            h = self._tokens(b)
            for j, i in enumerate(idx):
                res[i] = h[j][b['attention_mask'][j].bool()]
        return res

    @torch.inference_mode()
    def rerank(self, q, docs):
        qv = self.encode([q], 'q')[0]
        out = []
        for d in self.encode(docs, 'd'):
            s = (qv @ d.T).max(dim=1).values.sum()
            finite(s)
            out.append(float(s.float().cpu()))
        return out


def release():
    """Give cached memory back before another model loads."""
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
