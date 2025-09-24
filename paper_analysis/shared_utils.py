#!/usr/bin/env python3
"""
Shared utilities for PDF processing and LLM management

This module contains common utilities used by both the QA system and topic modeling system.
"""

import os
import json
import tiktoken
import PyPDF2
from typing import Dict, Any
from langchain_community.llms import OpenAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI


class PDFProcessor:
    """Handles PDF text extraction"""

    @staticmethod
    def extract_text_from_pdf(pdf_path: str) -> str:
        """Extract text from a PDF file"""
        try:
            with open(pdf_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
                return text.strip()
        except Exception as e:
            return f"Error extracting text from PDF: {str(e)}"


def load_config(config_file: str) -> Dict[str, Any]:
    """Load configuration from JSON file and environment variables"""
    with open(config_file, "r") as f:
        config = json.load(f)
    
    for _, provider_config in config.items():
        if "api_key_env" in provider_config:
            os.environ[provider_config["api_key_env"]] = provider_config["api_key"]
        
    return config


def create_llm(provider_name: str, config: Dict[str, Any]):
    """Create LangChain LLM based on configuration"""
    provider_name = provider_name.lower()

    if "model" not in config:
        raise ValueError(f"Model not specified in configuration for {provider_name}")

    model = config["model"]
    temperature = config.get("temperature", 0.7)
    max_tokens = config.get("max_output_tokens", 1000)

    print(f"Using {provider_name} with model: {model}")
    print(f"Temperature: {temperature}, Max tokens: {max_tokens}")

    if provider_name == "openai":
        return ChatOpenAI(
            openai_api_key=config["api_key"],
            model_name=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider_name == "gemini":
        return ChatGoogleGenerativeAI(
            google_api_key=config["api_key"],
            model=model,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
    elif provider_name == "anthropic":
        return ChatAnthropic(
            anthropic_api_key=config["api_key"],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider_name == "openai-completion":
        return OpenAI(
            openai_api_key=config["api_key"],
            model_name=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {provider_name}")


def get_use_chat_model(provider_name: str) -> bool:
    """Determine if the provider uses chat models or completion models"""
    return provider_name not in ["openai-completion"]


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken"""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except:
        # Fallback: rough estimation (1 token ≈ 4 characters)
        return len(text) // 4


def calculate_cost(input_tokens: int, output_tokens: int, pricing_config: Dict[str, Any]) -> float:
    """Calculate cost for a single request"""
    if "pricing_per_1k_tokens" not in pricing_config:
        return 0.0

    pricing = pricing_config["pricing_per_1k_tokens"]
    input_cost = (input_tokens / 1000) * pricing.get("input", 0)
    output_cost = (output_tokens / 1000) * pricing.get("output", 0)
    return input_cost + output_cost


def truncate_text(text: str, context_length: int, max_output_tokens: int) -> str:
    """Truncate text to fit within context length limits"""
    reserved_tokens = max_output_tokens + 1000  # Output tokens + buffer
    available_tokens = context_length - reserved_tokens

    if available_tokens <= 0:
        return text[:1000]  # Emergency fallback

    # Use character-based estimation (rough: 1 token ≈ 4 characters)
    estimated_text_tokens = len(text) // 4

    if estimated_text_tokens <= available_tokens:
        return text

    # Truncate text to fit (conservative approach)
    char_ratio = available_tokens / estimated_text_tokens
    truncated_length = int(len(text) * char_ratio * 0.8)  # 80% to be safe

    # Find a good breaking point (end of sentence)
    truncated_text = text[:truncated_length]
    last_period = max(
        truncated_text.rfind("."),
        truncated_text.rfind("!"),
        truncated_text.rfind("?"),
    )

    if last_period > truncated_length * 0.7:  # If we can find a good break point
        truncated_text = text[: last_period + 1]
    else:
        truncated_text = text[:truncated_length]

    return truncated_text + "\n\n[Text truncated due to context length limits]"