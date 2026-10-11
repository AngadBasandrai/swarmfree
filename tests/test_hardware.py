from swarm.hardware import fit_model


def test_separate_gpu_memory_is_not_summed_and_cpu_fallback_is_explicit():
    hw = dict(unified_memory=False, ram_available_gb=24, gpus=[dict(free_gb=4), dict(free_gb=4)])
    model = dict(name="large", size=6*1024**3)
    budgets = dict(ram_gb=16, vram_gb=8, context=4096, allow_cpu=False)
    assert not fit_model(model, hw, budgets)["fits"]
    budgets["allow_cpu"] = True
    assert fit_model(model, hw, budgets)["placement"] == "cpu"


def test_already_loaded_other_models_count_against_vram_budget():
    hw = dict(unified_memory=False, ram_available_gb=24, gpus=[dict(free_gb=6)])
    model = dict(name="new", size=3*1024**3)
    budgets = dict(ram_gb=16, vram_gb=6, context=4096, allow_cpu=False)
    assert fit_model(model, hw, budgets)["fits"]
    assert not fit_model(model, hw, budgets, [dict(name="other", size_vram=3*1024**3)])["fits"]


def test_same_resident_model_does_not_need_its_memory_allocated_twice():
    hw = dict(unified_memory=False, ram_available_gb=12, gpus=[dict(free_gb=1)])
    model = dict(name="current", size=3*1024**3)
    budgets = dict(ram_gb=8, vram_gb=6, context=4096, allow_cpu=False)
    assert fit_model(model, hw, budgets, [dict(name="current", size=4*1024**3, size_vram=4*1024**3)])["fits"]


def test_unified_memory_models_share_the_ram_budget():
    hw = dict(unified_memory=True, ram_available_gb=12, gpus=[])
    model = dict(name="new", size=3*1024**3)
    budgets = dict(ram_gb=6, vram_gb=0, context=4096, allow_cpu=True)
    assert not fit_model(model, hw, budgets, [dict(name="other", size=3*1024**3, size_vram=3*1024**3)])["fits"]
