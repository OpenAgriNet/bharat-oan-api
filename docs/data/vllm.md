# Commands to run VLLM


## Mistral Small 3.2 24b

```
docker run -d \
  --name mistral-small-vllm-dev \
  --restart unless-stopped \
  --gpus '"device=2,3"' \
  -v /data/hf_cache/hub/models--mistralai--Mistral-Small-3.2-24B-Instruct-2506:/usr/local/model \
  -v /data/vllm_cache:/root/.cache/vllm \
  -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HUB_OFFLINE=1 \
  -e VLLM_USE_MODELSCOPE=0 \
  --ipc=host \
  -p 8001:8000 \
  vllm/vllm-openai:nightly \
    --model /usr/local/model \
    --trust-remote-code \
    --served-model-name mistral-small \
    --tokenizer_mode mistral \
    --load_format mistral \
    --config_format mistral \
    --max-model-len 128000 \
    --tensor-parallel-size 2 \
    --pipeline-parallel-size 1 \
    --enable-auto-tool-choice \
    --tool-call-parser mistral \
    --gpu-memory-utilization 0.9 \
    --max-num-batched-tokens 8192 \
    --max-num-seqs 32
```

## Test model

```
curl http://localhost:8001/v1/chat/completions  -H "Content-Type: application/json" -d '{"model": "mistral-small","messages": [{ "role": "user", "content": "Hello, what model are you?" }]}'
```

```
curl http://10.128.170.2:8000/v1/chat/completions  -H "Content-Type: application/json" -d '{"model": "gpt-oss-20b","messages": [{ "role": "user", "content": "Hello, what model are you?" }]}'
```