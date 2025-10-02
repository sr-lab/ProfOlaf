#!/usr/bin/env python3
"""
PDF Question-Answering System with LangChain LLM Support

This script processes PDFs in a folder and allows asking questions about each PDF
using different LLM providers through LangChain abstraction.
"""

import os
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from langchain.schema import HumanMessage, SystemMessage
from langchain.prompts import PromptTemplate
from shared_utils import (
    PDFProcessor,
    load_config,
    create_llm,
    get_use_chat_model,
    count_tokens,
    calculate_cost,
    truncate_text,
)


class PDFQASystem:
    """Main PDF Question-Answering System using LangChain"""

    def __init__(
        self,
        llm,
        use_chat_model: bool = True,
        context_length: int = 16385,
        max_output_tokens: int = 1000,
        provider: str = "openai",
        model: str = "gpt-3.5-turbo",
        pricing_config: Dict[str, Any] = None,
    ):
        self.llm = llm
        self.use_chat_model = use_chat_model
        self.context_length = context_length
        self.max_output_tokens = max_output_tokens
        self.provider = provider
        self.model = model
        self.pricing_config = pricing_config or {}
        self.pdf_processor = PDFProcessor()
        self.total_cost = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._cost_lock = threading.Lock()

        # Create prompt template
        self.prompt_template = PromptTemplate(
            input_variables=["text", "question"],
            template="""Based on the following text, please answer the question:

Text:
{text}

Question: {question}

Answer:""",
        )

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken"""
        return count_tokens(text)

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a single request"""
        return calculate_cost(input_tokens, output_tokens, self.pricing_config)

    def extract_token_usage(self, question, response) -> tuple[int, int]:
        """Extract token usage from API response"""
        try:
            # Try to get token usage from response metadata
            if hasattr(response, "response_metadata") and response.response_metadata:
                metadata = response.response_metadata
                if "token_usage" in metadata:
                    token_usage = metadata["token_usage"]
                    input_tokens = token_usage.get("prompt_tokens")
                    output_tokens = token_usage.get("completion_tokens")
                    if input_tokens is not None and output_tokens is not None:
                        return input_tokens, output_tokens

            # Try to get token usage from usage_metadata (OpenAI format)
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = response.usage_metadata
                input_tokens = getattr(usage, "input_tokens", None)
                output_tokens = getattr(usage, "output_tokens", None)
                if input_tokens is not None and output_tokens is not None:
                    return input_tokens, output_tokens

            # Try to get token usage from additional_kwargs (some providers)
            if hasattr(response, "additional_kwargs") and response.additional_kwargs:
                kwargs = response.additional_kwargs
                if "usage" in kwargs:
                    usage = kwargs["usage"]
                    input_tokens = usage.get("prompt_tokens")
                    output_tokens = usage.get("completion_tokens")
                    if input_tokens is not None and output_tokens is not None:
                        return input_tokens, output_tokens

            # Fallback: try to get from response object attributes directly
            if hasattr(response, "input_tokens") and hasattr(response, "output_tokens"):
                input_tokens = response.input_tokens
                output_tokens = response.output_tokens
                if input_tokens is not None and output_tokens is not None:
                    return input_tokens, output_tokens

            # If no token usage found, estimate using token counting
            print(
                "WARNING: Token usage not found in API response. Using estimated token counts for cost calculation."
            )
            answer = response.content if hasattr(response, "content") else str(response)
            input_tokens = self.count_tokens(question)
            output_tokens = self.count_tokens(answer)
            return input_tokens, output_tokens

        except Exception as e:
            print(
                f"WARNING: Error extracting token usage: {e}. Using estimated token counts for cost calculation."
            )
            answer = response.content if hasattr(response, "content") else str(response)
            input_tokens = self.count_tokens(question)
            output_tokens = self.count_tokens(answer)
            return input_tokens, output_tokens

    def get_cost_summary(self) -> Dict[str, Any]:
        """Get cost summary for all operations"""
        pricing = self.pricing_config.get("pricing_per_1k_tokens", {})
        return {
            "total_cost": round(self.total_cost, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "provider": self.provider,
            "model": self.model,
            "cost_per_1k_input": pricing.get("input", 0),
            "cost_per_1k_output": pricing.get("output", 0),
        }

    def reset_cost_tracking(self):
        """Reset cost tracking counters"""
        self.total_cost = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def ask_question(self, text: str, question: str) -> Dict[str, Any]:
        """Ask a question about the given text using LangChain"""
        try:
            # Truncate text if necessary
            truncated_text = truncate_text(
                text, self.context_length, self.max_output_tokens
            )

            # Make the API call
            if self.use_chat_model:
                system_message = "You are a helpful assistant that answers questions based on provided text."
                human_message = self.prompt_template.format(
                    text=truncated_text, question=question
                )
                messages = [
                    SystemMessage(content=system_message),
                    HumanMessage(content=human_message),
                ]
                response = self.llm.invoke(messages)
                answer = response.content.strip()
            else:
                prompt = self.prompt_template.format(
                    text=truncated_text, question=question
                )
                response = self.llm.invoke(prompt)
                answer = response.strip()

            # Extract token usage from API response
            input_tokens, output_tokens = self.extract_token_usage(question, response)

            # Calculate cost
            cost = self.calculate_cost(input_tokens, output_tokens)

            # Update totals (thread-safe)
            with self._cost_lock:
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens
                self.total_cost += cost

            return {
                "answer": answer,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": round(cost, 6),
            }
        except Exception as e:
            print(f"WARNING: Error calling LLM: {str(e)}")
            return {
                "answer": f"Error calling LLM: {str(e)}",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
            }

    def process_single_pdf(
        self, pdf_file: Path, prompts: List[Dict[str, str]]
    ) -> tuple[str, Dict[str, Any]]:
        """Process a single PDF file and return results"""
        try:
            print(f"Processing: {pdf_file.name}")
            text = self.pdf_processor.extract_text_from_pdf(str(pdf_file))

            if text.startswith("Error"):
                print(f"  Error: {text}")
                return pdf_file.name, {"error": text}

            pdf_results = {"text_length": len(text)}
            answers = {}
            pdf_cost = 0.0

            for i, prompt in enumerate(prompts, 1):
                print(f"  Processing prompt {i}/{len(prompts)}: {prompt['filename']}")
                response = self.ask_question(text, prompt["content"])
                answers[f"prompt_{i}"] = {
                    "filename": prompt["filename"],
                    "prompt": prompt["content"],
                    "answer": response["answer"],
                    "input_tokens": response["input_tokens"],
                    "output_tokens": response["output_tokens"],
                    "cost": response["cost"],
                }
                pdf_cost += response["cost"]
                print(
                    f"    Cost: ${response['cost']:.6f} ({response['input_tokens']} input + {response['output_tokens']} output tokens)"
                )

            pdf_results["answers"] = answers
            pdf_results["total_cost"] = round(pdf_cost, 6)
            print(f"  PDF total cost: ${pdf_cost:.6f}")
            print()

            return pdf_file.name, pdf_results

        except Exception as e:
            error_msg = f"Error processing {pdf_file.name}: {str(e)}"
            print(f"  {error_msg}")
            return pdf_file.name, {"error": error_msg}

    def process_pdf_folder(
        self,
        pdf_folder: str,
        prompts: List[Dict[str, str]],
        output_file: Optional[str] = None,
        max_workers: int = 4,
    ) -> Dict[str, Any]:
        """Process all PDFs in a folder and answer questions about each"""
        pdf_folder = Path(pdf_folder)
        results = {}

        if not output_file:
            raise ValueError("Output file is required")

        if not pdf_folder.exists():
            raise ValueError(f"PDF folder does not exist: {pdf_folder}")

        pdf_files = list(pdf_folder.glob("*.pdf"))
        if not pdf_files:
            print(f"No PDF files found in {pdf_folder}")
            return results

        pricing = self.pricing_config.get("pricing_per_1k_tokens", {})
        print(f"Found {len(pdf_files)} PDF files to process...")
        print(f"Using {self.provider} with model: {self.model}")
        print(
            f"Pricing: ${pricing.get('input', 0):.6f}/1K input, ${pricing.get('output', 0):.6f}/1K output"
        )
        print(f"Processing with {max_workers} parallel workers")
        print("-" * 60)

        # Reset cost tracking for parallel processing
        self.reset_cost_tracking()

        # Process PDFs in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all PDF processing tasks
            future_to_pdf = {
                executor.submit(self.process_single_pdf, pdf_file, prompts): pdf_file
                for pdf_file in pdf_files
            }

            # Collect results as they complete
            for future in as_completed(future_to_pdf):
                pdf_file = future_to_pdf[future]
                try:
                    pdf_name, pdf_results = future.result()
                    results[pdf_name] = pdf_results
                except Exception as e:
                    error_msg = f"Error processing {pdf_file.name}: {str(e)}"
                    print(f"  {error_msg}")
                    results[pdf_file.name] = {"error": error_msg}

        # Add cost summary to results
        cost_summary = self.get_cost_summary()
        results["_cost_summary"] = cost_summary

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"Results saved to: {output_file}")

        print("=" * 60)
        print("COST SUMMARY")
        print("=" * 60)
        print(f"Total cost: ${cost_summary['total_cost']:.6f}")
        print(f"Total input tokens: {cost_summary['total_input_tokens']:,}")
        print(f"Total output tokens: {cost_summary['total_output_tokens']:,}")
        print(f"Provider: {cost_summary['provider']}")
        print(f"Model: {cost_summary['model']}")

        return results

    def ask_single_prompt(self, pdf_path: str, prompt: str) -> Dict[str, Any]:
        """Ask a single prompt about a specific PDF"""
        text = self.pdf_processor.extract_text_from_pdf(pdf_path)
        if text.startswith("Error"):
            return {"answer": text, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
        return self.ask_question(text, prompt)


def main():
    parser = argparse.ArgumentParser(
        description="PDF Question-Answering System with LangChain"
    )
    parser.add_argument(
        "prompts_folder",
        help="Folder containing prompt files (one prompt per .txt file)",
    )
    parser.add_argument(
        "--config",
        default="paper_analysis/llm_config.json",
        help="Configuration file for LLM settings",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "gemini", "anthropic", "openai-completion"],
        help="LLM provider to use",
    )
    parser.add_argument("--output", help="Output file to save results (JSON format)")
    parser.add_argument("--pdf-folder", help="Path to folder containing PDF files")
    parser.add_argument(
        "--single-pdf", help="Process a single PDF file instead of folder"
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=16385,
        help="Maximum context length in tokens (default: 16385)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximum number of parallel workers for PDF processing (default: 1)",
    )

    args = parser.parse_args()

    # Load configuration
    if not os.path.exists(args.config):
        print(f"Configuration file not found: {args.config}")
        print("Please create a configuration file with your API keys. Example:")
        print('{"openai": {"api_key": "your-key-here", "model": "gpt-3.5-turbo"}}')
        return

    config = load_config(args.config)

    # Get prompts from folder
    prompts_folder = Path(args.prompts_folder)
    if not prompts_folder.exists():
        print(f"Prompts folder does not exist: {prompts_folder}")
        return

    prompt_files = list(prompts_folder.glob("*.txt"))
    if not prompt_files:
        print(f"No .txt files found in {prompts_folder}")
        return

    prompts = []
    for prompt_file in sorted(prompt_files):
        with open(prompt_file, "r", encoding="utf-8") as f:
            prompt_content = f.read().strip()
            if prompt_content:
                prompts.append(
                    {"filename": prompt_file.name, "content": prompt_content}
                )

    if not prompts:
        print("No valid prompts found in the folder")
        return

    # Create LLM
    try:
        provider_config = config[args.provider]
        llm = create_llm(args.provider, provider_config)
        use_chat_model = get_use_chat_model(args.provider)
        max_output_tokens = provider_config.get("max_tokens", 1000)
    except KeyError:
        print(f"Configuration for {args.provider} not found in {args.config}")
        return
    except Exception as e:
        print(f"Error creating LLM: {e}")
        return

    # Create PDF QA system
    qa_system = PDFQASystem(
        llm,
        use_chat_model,
        args.context_length,
        max_output_tokens,
        args.provider,
        provider_config["model"],
        provider_config,
    )

    # Process PDFs
    if args.single_pdf:
        pricing = provider_config.get("pricing_per_1k_tokens", {})
        print(f"Processing single PDF: {args.single_pdf}")
        print(f"Using {args.provider} with model: {provider_config['model']}")
        print(
            f"Pricing: ${pricing.get('input', 0):.6f}/1K input, ${pricing.get('output', 0):.6f}/1K output"
        )
        print("-" * 60)

        for prompt in prompts:
            print(f"Prompt: {prompt['filename']}")
            print(f"Content: {prompt['content'][:100]}...")
            response = qa_system.ask_single_prompt(args.single_pdf, prompt["content"])
            print(f"Answer: {response['answer']}")
            print(
                f"Cost: ${response['cost']:.6f} ({response['input_tokens']} input + {response['output_tokens']} output tokens)"
            )
            print("-" * 50)

        # Display cost summary for single PDF processing
        cost_summary = qa_system.get_cost_summary()
        print("=" * 60)
        print("COST SUMMARY")
        print("=" * 60)
        print(f"Total cost: ${cost_summary['total_cost']:.6f}")
        print(f"Total input tokens: {cost_summary['total_input_tokens']:,}")
        print(f"Total output tokens: {cost_summary['total_output_tokens']:,}")
        print(f"Provider: {cost_summary['provider']}")
        print(f"Model: {cost_summary['model']}")

    elif args.pdf_folder:
        results = qa_system.process_pdf_folder(
            args.pdf_folder, prompts, args.output, args.max_workers
        )
        print(
            f"Processed {len([k for k in results.keys() if not k.startswith('_')])} PDF files"
        )
    else:
        print("Use --single-pdf or --pdf-folder")


if __name__ == "__main__":
    main()