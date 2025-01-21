import os
import re
import json
import torch
import folder_paths

from .loader import load_flux_mod
from .sampler import common_ksampler
import comfy.samplers 
import comfy.cli_args
from comfy import model_management


def using_scaled_fp8(model_patcher):
    return (comfy.cli_args.args.fast and
        model_management.supports_fp8_compute(model_patcher.load_device)) or model_patcher.model.model_config.scaled_fp8

class FluxModCheckpointLoader:
    @classmethod
    def INPUT_TYPES(s):
        checkpoint_paths = folder_paths.get_filename_list("checkpoints")
        if "unet_gguf" in folder_paths.folder_names_and_paths:
            checkpoint_paths = checkpoint_paths + folder_paths.get_filename_list("unet_gguf")
        return {
            "required": {
                "ckpt_name": (checkpoint_paths,),
                "guidance_name": (folder_paths.get_filename_list("checkpoints"),),
                "quant_mode": (["bf16", "float8_e4m3fn (8 bit)", "float8_e5m2 (also 8 bit)"],),
            }
        }
    

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load_checkpoint"
    CATEGORY = "ExtraModels/FluxMod"
    TITLE = "FluxModCheckpointLoader"

    def load_checkpoint(self, *, ckpt_name, quant_mode, guidance_name=None,lite_patch_ckpt_name=None):
        dtypes = {
            "bf16": torch.bfloat16, 
            "float8_e4m3fn (8 bit)": torch.float8_e4m3fn, 
            "float8_e5m2 (also 8 bit)": torch.float8_e5m2
        }

        is_gguf = ckpt_name.lower().endswith(".gguf")
        ckpt_path = folder_paths.get_full_path("unet_gguf" if is_gguf else "checkpoints", ckpt_name)
        if guidance_name is not None:
            guidance_path = folder_paths.get_full_path("checkpoints", guidance_name)
        else:
            guidance_path = None
        if lite_patch_ckpt_name is not None:
            lite_patch_ckpt_name = folder_paths.get_full_path("checkpoints", lite_patch_ckpt_name)
        flux_mod = load_flux_mod(
            model_path = ckpt_path,
            timestep_guidance_path = guidance_path,
            linear_dtypes=dtypes[quant_mode],
            lite_patch_path=lite_patch_ckpt_name,
            is_gguf=is_gguf,
        )
        return (flux_mod,)

class FluxModCheckpointLoaderMini(FluxModCheckpointLoader):
    @classmethod
    def INPUT_TYPES(s):
        result = super().INPUT_TYPES()
        result["required"] |= {
            "lite_patch_ckpt_name": (folder_paths.get_filename_list("checkpoints"),),
        }
        return result

    TITLE = "FluxModCheckpointLoaderMini"

class ChromaCheckpointLoader(FluxModCheckpointLoader):
    @classmethod
    def INPUT_TYPES(s):
        result = super().INPUT_TYPES()
        del result["required"]["guidance_name"]
        return result

    TITLE = "ChromaCheckpointLoader"

class ModelMover:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "samples": ("MODEL", ),
            }
        }
    

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load_checkpoint"
    CATEGORY = "ExtraModels/FluxMod"
    TITLE = "FluxModCheckpointLoader"

    def load_checkpoint(self, ckpt_name, guidance_name, quant_mode):
        dtypes = {
            "bf16": torch.bfloat16, 
            "float8_e4m3fn (8 bit)": torch.float8_e4m3fn, 
            "float8_e5m2 (also 8 bit)": torch.float8_e5m2
        }
            
        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
        guidance_path = folder_paths.get_full_path("checkpoints", guidance_name)
        flux_mod = load_flux_mod(
            model_path = ckpt_path,
            timestep_guidance_path = guidance_path,
            linear_dtypes=dtypes[quant_mode]
        )
        return (flux_mod,)
    
class SkipLayerForward:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", ),
                "skip_mmdit_layers": ("STRING", {"default": "10", "multiline": False}), 
                "skip_dit_layers": ("STRING", {"default": "3, 4", "multiline": False})
            }
        }
    
    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "skip_layer"
    CATEGORY = "ExtraModels/FluxMod"
    TITLE = "SkipLayerForward"

    DESCRIPTION = "Prune model layers"

    def skip_layer(self, model, skip_mmdit_layers, skip_dit_layers):
        
        skip_mmdit_layers = re.split(r"\s*,\s*", skip_mmdit_layers)
        skip_mmdit_layers = [int(num) for num in skip_mmdit_layers]

        skip_dit_layers = re.split(r"\s*,\s*", skip_dit_layers)
        skip_dit_layers = [int(num) for num in skip_dit_layers]

        model.model.diffusion_model.skip_dit = skip_dit_layers
        model.model.diffusion_model.skip_mmdit = skip_mmdit_layers
        return (model, )
    

class KSamplerMod:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "The model used for denoising the input latent."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "tooltip": "The random seed used for creating the noise."}),
                "steps": ("INT", {"default": 20, "min": 1, "max": 10000, "tooltip": "The number of steps used in the denoising process."}),
                "cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step":0.1, "round": 0.01, "tooltip": "The Classifier-Free Guidance scale balances creativity and adherence to the prompt. Higher values result in images more closely matching the prompt however too high values will negatively impact quality."}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS, {"tooltip": "The algorithm used when sampling, this can affect the quality, speed, and style of the generated output."}),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS, {"tooltip": "The scheduler controls how noise is gradually removed to form the image."}),
                "positive": ("CONDITIONING", {"tooltip": "The conditioning describing the attributes you want to include in the image."}),
                "negative": ("CONDITIONING", {"tooltip": "The conditioning describing the attributes you want to exclude from the image."}),
                "latent_image": ("LATENT", {"tooltip": "The latent image to denoise."}),
                "denoise": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "The amount of denoising applied, lower values will maintain the structure of the initial image allowing for image to image sampling."}),
                "activation_casting": (["bf16", "fp16"], {"default": "bf16", "tooltip": "cast model activation to bf16 or fp16, always use bf16 unless your card does not supports it"})
            }
        }

    RETURN_TYPES = ("LATENT",)
    OUTPUT_TOOLTIPS = ("The denoised latent.",)
    FUNCTION = "sample"
    CATEGORY = "ExtraModels/FluxMod"
    DESCRIPTION = "Uses the provided model, positive and negative conditioning to denoise the latent image."
    TITLE = "KSamplerMod"

    def sample(self, model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise=1.0, activation_casting="bf16"):
        if using_scaled_fp8(model):
            return common_ksampler(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise=denoise)
        dtypes = {
            "bf16": torch.bfloat16, 
            "fp16": torch.float16
        }
        with torch.autocast(device_type="cuda", dtype=dtypes[activation_casting]):
            return common_ksampler(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise=denoise)

class FluxModSamplerWrapperNode:
    RETURN_TYPES = ("SAMPLER",)
    FUNCTION = "go"
    CATEGORY = "ExtraModels/FluxMod"
    TITLE = "FluxModSamplerWrapper"
    DESCRIPTION = "Enables FluxMod in float8 quant_mode to be used with advanced sampling nodes by wrapping another SAMPLER. If you are using multiple sampler wrappers, put this node closest to SamplerCustom/SamplerCustomAdvanced/etc."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "sampler": ("SAMPLER",),
                "activation_casting": (
                    ("bf16", "fp16"),
                    {
                        "default": "bf16",
                        "tooltip": "Cast model activation to bf16 or fp16. Always use bf16 unless your card does not support it."
                    },
                ),
            },
        }

    @classmethod
    def go(cls, *, sampler, activation_casting):
        dtype = torch.bfloat16 if activation_casting == "bf16" else torch.float16
        def wrapper(model, *args, **kwargs):
            if using_scaled_fp8(model.inner_model.model_patcher):
                return sampler.sampler_function(model, *args, **kwargs)
            with torch.autocast(device_type=model_management.get_torch_device().type, dtype=dtype):
                return sampler.sampler_function(model, *args, **kwargs)
        sampler_wrapper = comfy.samplers.KSAMPLER(wrapper, extra_options=sampler.extra_options, inpaint_options=sampler.inpaint_options)
        return (sampler_wrapper,)

    
NODE_CLASS_MAPPINGS = {
    "FluxModCheckpointLoader" : FluxModCheckpointLoader,
    "FluxModCheckpointLoaderMini": FluxModCheckpointLoaderMini,
    "ChromaCheckpointLoader": ChromaCheckpointLoader,
    "KSamplerMod": KSamplerMod,
    "FluxModSamplerWrapper": FluxModSamplerWrapperNode,
    "SkipLayerForward": SkipLayerForward
}
