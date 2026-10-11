"""Optional CUDA LoRA trainer. Run inside a separately installed training environment."""
def main():
    import hashlib
    import json
    import os
    from pathlib import Path
    import sys
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["WANDB_DISABLED"] = "true"
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer
    if not torch.cuda.is_available(): raise RuntimeError("A supported CUDA GPU is required")
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rows = [json.loads(v) for v in Path(config["dataset_path"]).read_text(encoding="utf-8").splitlines()]
    tokenizer = AutoTokenizer.from_pretrained(config["base_directory"], local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token is None: tokenizer.pad_token = tokenizer.eos_token
    # Render a plain conversation if the selected tokenizer has no chat template.
    texts = [dict(text=tokenizer.apply_chat_template(r["messages"], tokenize=False) if tokenizer.chat_template else "\n".join(m["role"]+": "+m["content"] for m in r["messages"])) for r in rows]
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16) if config["quantized"] else None
    model = AutoModelForCausalLM.from_pretrained(config["base_directory"], local_files_only=True, trust_remote_code=False,
        quantization_config=quantization, device_map={"":0}, dtype=torch.float16)
    args = SFTConfig(output_dir=config["output_directory"], max_steps=config["max_steps"], per_device_train_batch_size=1,
        gradient_accumulation_steps=4, learning_rate=0.0002, max_length=config["sequence_length"], dataset_text_field="text",
        gradient_checkpointing=True, fp16=True, report_to="none", save_strategy="no", logging_steps=5)
    trainer = SFTTrainer(model=model, args=args, train_dataset=Dataset.from_list(texts), processing_class=tokenizer,
        peft_config=LoraConfig(r=config["rank"], lora_alpha=config["rank"]*2, target_modules="all-linear", task_type="CAUSAL_LM"))
    trainer.train(); trainer.save_model(config["output_directory"]); tokenizer.save_pretrained(config["output_directory"])
    print("Local adapter saved; evaluate before promotion")


if __name__ == "__main__": main()
