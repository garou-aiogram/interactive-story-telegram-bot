#!/usr/bin/env bash
# ЕДИНСТВЕННОЕ МЕСТО, ГДЕ ЗАДАЁТСЯ МОДЕЛЬ.
# Меняешь путь к .gguf — бот не трогаешь вообще.

MODEL_PATH="/opt/models/your-model.gguf"

CTX_TOTAL=32768   # общий контекст, делится между слотами
PARALLEL=4        # слотов = одновременных юзеров. ctx на юзера = CTX_TOTAL/PARALLEL
GPU_LAYERS=999    # 999 = всё на GPU; 0 = только CPU

./llama-server \
  --model "$MODEL_PATH" \
  --host 0.0.0.0 \
  --port 8080 \
  --ctx-size   $CTX_TOTAL \
  --parallel   $PARALLEL \
  --n-gpu-layers $GPU_LAYERS \
  --flash-attn \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  --alias local-model
