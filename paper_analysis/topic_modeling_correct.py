
#!/usr/bin/env python3
"""
PDF Topic Modeling System with TopicGPT and LangChain LLM Support

This script processes PDFs in a folder and performs topic modeling using TopicGPT
with different LLM providers through LangChain abstraction. Data extraction from PDFs
is done automatically when needed for each step.
"""

import os
import json
import traceback
import argparse
import re
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from shared_utils import (
    PDFProcessor,
    load_config,
    create_llm,
    get_use_chat_model,
    truncate_text,
)

from topic_modelling import (
    TopicModelingSystem,
    TopicModelingCorrect,
    TOPICGPT_AVAILABLE,
)

with open("analysis_conf.json", "r") as f:
    analysis_conf = json.load(f)


def show_detailed_help():
    """Show detailed help with examples and workflow"""
    help_text = """
PDF Topic Modeling System with TopicGPT and LangChain
====================================================

OVERVIEW
--------
This system processes PDF documents and performs hierarchical topic modeling using TopicGPT
with different LLM providers through LangChain abstraction. The system automatically
extracts text from PDFs when needed for each step.

WORKFLOW
--------
The topic modeling process follows these sequential steps:

1. level1    - Generate high-level topics from PDF documents
   Arguments: --prompt-file (optional), --seed-file (optional)

Each step must be run in order, and data extraction is done automatically when needed.

USAGE
-----
python topic_modeling_system.py <pdf_folder> --output-dir <output_dir> --step <step> [options]

REQUIRED ARGUMENTS
------------------
pdf_folder          Folder containing PDF files to process
--output-dir        Output directory to save results and intermediate files
--seed-file         Path to seed file for level1 or level2 topic generation
--prompt-file       Path to prompt file for the current step

OPTIONAL ARGUMENTS
------------------
--config            Configuration file for LLM settings (default: llm_config.json)
--provider          LLM provider: openai, gemini, anthropic, openai-completion (default: openai)
--context-length    Maximum context length in tokens (default: 16385)
--max-workers       Number of parallel workers for PDF processing (default: 4)

EXAMPLES
--------

CONFIGURATION FILE
------------------
Create a configuration file (llm_config.json) with your API keys:

{
  "openai": {
    "api_key": "your-openai-api-key",
    "model": "gpt-3.5-turbo",
    "temperature": 0.7,
    "max_output_tokens": 1000,
    "context_length": 16385
  },
  "gemini": {
    "api_key": "your-gemini-api-key",
    "model": "gemini-pro",
    "temperature": 0.7,
    "max_output_tokens": 1000,
    "context_length": 32768
  },
  "anthropic": {
    "api_key": "your-anthropic-api-key",
    "model": "claude-3-sonnet-20240229",
    "temperature": 0.7,
    "max_output_tokens": 1000,
    "context_length": 200000
  }
}

OUTPUT FILES
------------
The system creates various output files in the specified output directory:

- data.jsonl              - Extracted text from PDFs in TopicGPT format
- topics_lvl1.md          - Generated high-level topics
- topics_lvl2.md          - Generated detailed sub-topics
- topics_refined.md       - Refined topics after merging/removing
- assignments.jsonl       - Topic assignments for each document
- corrected_assignments.jsonl - Corrected topic assignments
- generation_lvl1.json    - Raw generation data for level1
- generation_lvl2.json    - Raw generation data for level2
- refinement.json         - Refined topics in JSON format
- mapping.json            - Topic mapping after refinement

PROMPT FILES
------------
You can provide custom prompt files for each step. If not provided, the system
looks for default prompt files in the output directory:

- prompt_lvl1.txt         - Prompt for level1 topic generation
- prompt_lvl2.md          - Prompt for level2 topic generation
- prompt_refine.txt       - Prompt for topic refinement
- prompt_assign.txt       - Prompt for topic assignment
- prompt_correct.txt      - Prompt for assignment correction

SEED FILES
----------
Seed files provide initial topics or guidance for generation:

- seed_lvl1.md            - Initial topics for level1 generation
- seed_lvl2.md            - Level1 topics as seed for level2 generation

For more information, see the project documentation or contact support.
"""
    print(help_text)


def parse_args():
    parser = argparse.ArgumentParser(description='PDF Topic Modeling System with TopicGPT and LangChain - Single Step Execution (Data extraction is automatic): Level 1')
    parser.add_argument('pdf_folder', help='Folder containing PDF files to process', default=analysis_conf["articles_folder"])
    parser.add_argument('--output-dir', help='Output directory to save results and intermediate files', default=analysis_conf["output_path"])
    parser.add_argument('--config', help='Configuration file for LLM settings', default="paper_analysis/llm_config.json")
    parser.add_argument('--provider', help='LLM provider to use', default="openai")
    parser.add_argument('--context-length', help='Maximum context length in tokens', default=16385)
    parser.add_argument('--max-workers', help='Maximum number of parallel workers for PDF processing', default=4)
    parser.add_argument("--topic-file", help='Path to topic file')
    parser.add_argument("--prompt-file", help='Path to prompt file for level1 topic generation')
    parser.add_argument("--help-detailed", help='Show detailed help with examples and workflow', action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()
    if args.help_detailed:
        show_detailed_help()
        return
    
    if not TOPICGPT_AVAILABLE:
        print("ERROR: TopicGPT is required but not installed. Install with: pip install topicgpt_python")
        return
    if not os.path.exists(args.config):
        print(f"Configuration file not found: {args.config}")
        print("Please create a configuration file with your API keys.")
        return
    if not os.path.exists(args.topic_file):
        print(f"Topic file not found: {args.topic_file}")
        print("Please create a seed file with your topics.")
        return
    
    config = load_config(args.config)
    try:
        provider_config = config[args.provider]
        llm = create_llm(args.provider, provider_config)
        use_chat_model = get_use_chat_model(args.provider)
        max_output_tokens = provider_config.get("max_output_tokens", 1000)
        context_length = provider_config.get("context_length", args.context_length)
    except KeyError:
        print(f"Configuration for {args.provider} not found in {args.config}")
        return
    except Exception as e:
        print(f"Error creating LLM: {e}")
        return
    
    topic_modeling_system = TopicModelingSystem(
        TopicModelingCorrect(
            llm,
            use_chat_model,
            context_length,
            max_output_tokens,
            args.provider,
            provider_config["model"],
            provider_config.get("temperature", 0.7),
        )
    )

    result = topic_modeling_system.execute_step(
        args.pdf_folder,
        args.output_dir,
        max_workers=args.max_workers,
        topic_file=args.topic_file,
        prompt_file=args.prompt_file,
    )

    if result.get("success", False):
        print(f"Step 'correct' completed successfully!")
        if "message" in result:
            print(result["message"])
    else:
        print(f"Step 'correct' failed: {result.get('error', 'Unknown error')}")
        return

if __name__ == "__main__":
    main()