# Windows-вариант. Тот же смысл: путь к .gguf задаётся здесь.
$MODEL_PATH = "C:\models\your-model.gguf"

.\llama-server.exe `
  --model $MODEL_PATH `
  --host 127.0.0.1 `
  --port 8080 `
  --ctx-size 32768 `
  --parallel 4 `
  --n-gpu-layers 999 `
  --flash-attn `
  --cont-batching `
  --alias local-model
