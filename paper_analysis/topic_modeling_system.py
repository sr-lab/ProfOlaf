
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

# TopicGPT imports
try:
    from topicgpt_python import (
        generate_topic_lvl1,
        generate_topic_lvl2,
        refine_topics,
        assign_topics,
        correct_topics,
    )

    TOPICGPT_AVAILABLE = True
except ImportError:
    print("WARNING: TopicGPT not installed. Install with: pip install topicgpt_python")
    TOPICGPT_AVAILABLE = False


with open("analysis_conf.json", "r") as f:
    analysis_conf = json.load(f)

def parse_topic_text(topic_text: str) -> List[Dict[str, Any]]:
    """Parse topic text format into dictionary structure.
    
    Expected format: [1] Topic Name (Count: X): Description
    Returns: List of dicts with 'topic_name', 'count', 'description' keys
    """
    topics = []
    lines = topic_text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Match pattern: [1] Topic Name (Count: X): Description
        pattern = r'\[(\d+)\]\s+(.+?)\s+\(Count:\s*(\d+)\):\s*(.+)'
        match = re.match(pattern, line)
        
        if match:
            level, topic_name, count, description = match.groups()
            topics.append({
                'level': int(level),
                'topic_name': topic_name.strip(),
                'count': int(count),
                'description': description.strip()
            })
    
    return topics


def topics_to_text(topics: List[Dict[str, Any]]) -> str:
    """Convert topic dictionaries back to text format.
    
    Converts list of topic dicts to the format: [1] Topic Name (Count: X): Description
    """
    lines = []
    for topic in topics:
        line = f"[{topic['level']}] {topic['topic_name']} (Count: {topic['count']}): {topic['description']}"
        lines.append(line)
    return '\n'.join(lines)


class TopicModelingSystem:
    """Main Topic Modeling System using TopicGPT and LangChain"""

    def __init__(
        self,
        llm,
        use_chat_model: bool = True,
        context_length: int = 16385,
        max_output_tokens: int = 1000,
        provider: str = "openai",
        model: str = "gpt-3.5-turbo",
        temperature: float = 0.7,
    ):
        if not TOPICGPT_AVAILABLE:
            raise ImportError(
                "TopicGPT is required but not installed. Install with: pip install topicgpt_python"
            )

        self.llm = llm
        self.use_chat_model = use_chat_model
        self.context_length = context_length
        self.max_output_tokens = max_output_tokens
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.pdf_processor = PDFProcessor()
        
        # Common TopicGPT parameters
        self.api = provider
        self.topicgpt_model = model

    def prepare_data_for_topicgpt(self, pdf_texts: List[Dict[str, str]]) -> str:
        """Prepare data in TopicGPT format"""
        data_lines = []
        for i, pdf_data in enumerate(pdf_texts):
            # Truncate text to fit within context length limits
            truncated_text = truncate_text(
                pdf_data["text"], self.context_length, self.max_output_tokens
            )
            data_entry = {
                "id": f"pdf_{i}",
                "text": truncated_text,
                "filename": pdf_data["filename"],
            }
            data_lines.append(json.dumps(data_entry))

        return "\n".join(data_lines)

    def generate_topics_level1(
        self, output_dir: str, prompt_file: str = None, seed_file: str = None
    ) -> Dict[str, Any]:
        """Generate high-level topics using TopicGPT"""
        try:
            print("Generating high-level topics...")

            if not prompt_file:
                prompt_file = os.path.join(output_dir, "prompt_lvl1.txt")
                if not os.path.exists(prompt_file):
                    return {
                        "error": f"Prompt file not found: {prompt_file}. Please provide a prompt file for level 1 topic generation."
                    }

            # Set up TopicGPT parameters
            data_file = os.path.join(output_dir, "data.jsonl")
            if not seed_file:
                seed_file = os.path.join(output_dir, "seed_lvl1.md")
                # Create empty seed file if none provided
                with open(seed_file, "w", encoding="utf-8") as f:
                    f.write("")
            topic_file = os.path.join(output_dir, "topics_lvl1.md")
            out_file = os.path.join(output_dir, "generation_lvl1.json")

            # Generate topics
            generate_topic_lvl1(
                api=self.api,
                model=self.topicgpt_model,
                data=data_file,
                prompt_file=prompt_file,
                seed_file=seed_file,
                out_file=out_file,
                topic_file=topic_file,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                verbose=True,
            )

            # Load generated topics
            with open(topic_file, "r", encoding="utf-8") as f:
                topic_text = f.read()
                topics = parse_topic_text(topic_text)

            print(f"Generated {len(topics)} high-level topics")
            return {
                "topics": topics,
                "topic_file": topic_file,
                "generation_file": out_file,
                "success": True,
            }

        except Exception as e:
            traceback.print_exc()
            print(f"Error generating level 1 topics: {str(e)}")
            return {"error": str(e), "success": False}

    def generate_topics_level2(
        self, level1_topics: List[Dict[str, Any]], output_dir: str, prompt_file: str = None, seed_file: str = None
    ) -> Dict[str, Any]:
        """Generate low-level topics using TopicGPT"""
        try:
            print("Generating low-level topics...")

            if not prompt_file:
                prompt_file = os.path.join(output_dir, "prompt_lvl2.md")
                if not os.path.exists(prompt_file):
                    return {
                        "error": f"Prompt file not found: {prompt_file}. Please provide a prompt file for level 2 topic generation."
                    }

            # Set up TopicGPT parameters
            data_file = os.path.join(output_dir, "generation_lvl1.json")
            if not seed_file:
                seed_file = os.path.join(output_dir, "seed_lvl2.md")
                # Create seed file with level 1 topics if no seed file provided
                with open(seed_file, "w", encoding="utf-8") as f:
                    f.write(topics_to_text(level1_topics))

            topic_file = os.path.join(output_dir, "topics_lvl2.md")
            out_file = os.path.join(output_dir, "generation_lvl2.json")

            # Generate topics
            generate_topic_lvl2(
                api=self.api,
                model=self.topicgpt_model,
                seed_file=seed_file,
                data=data_file,
                prompt_file=prompt_file,
                out_file=out_file,
                topic_file=topic_file,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                verbose=True,
            )

            # Load generated topics
            with open(topic_file, "r", encoding="utf-8") as f:
                topic_text = f.read()
                topics = parse_topic_text(topic_text)

            print(f"Generated {len(topics)} low-level topics")
            return {
                "topics": topics,
                "topic_file": topic_file,
                "generation_file": out_file,
                "success": True,
            }

        except Exception as e:
            traceback.print_exc()
            print(f"Error generating level 2 topics: {str(e)}")
            return {"error": str(e), "success": False}

    def refine_generated_topics(
        self, topic_file: str, output_dir: str, prompt_file: str = None
    ) -> Dict[str, Any]:
        """Refine topics by merging similar ones and removing irrelevant ones"""
        try:
            print("Refining topics...")

            if not prompt_file:
                prompt_file = os.path.join(output_dir, "prompt_refine.txt")
                if not os.path.exists(prompt_file):
                    return {
                        "error": f"Prompt file not found: {prompt_file}. Please provide a prompt file for topic refinement."
                    }

            # Set up TopicGPT parameters
            generation_file = os.path.join(output_dir, "generation_lvl1.json")
            refined_topic_file = os.path.join(output_dir, "refinement.json")
            out_file = os.path.join(output_dir, "topics_refined.md")
            mapping_file = os.path.join(output_dir, "mapping.json")

            # Refine topics
            refine_topics(
                api=self.api,
                model=self.topicgpt_model,
                prompt_file=prompt_file,
                generation_file=generation_file,
                topic_file=topic_file,
                out_file=out_file,
                updated_file=refined_topic_file,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                verbose=True,
                remove=True,
                mapping_file=mapping_file,
            )

            # Load refined topics
            with open(refined_topic_file, "r", encoding="utf-8") as f:
                topic_text = f.read()
                topics = parse_topic_text(topic_text)

            print(f"Refined to {len(topics)} topics")
            return {
                "topics": topics,
                "topic_file": refined_topic_file,
                "mapping_file": mapping_file,
                "success": True,
            }

        except Exception as e:
            traceback.print_exc()
            print(f"Error refining topics: {str(e)}")
            return {"error": str(e), "success": False}

    def assign_topics_to_documents(
        self, topic_file: str, output_dir: str, prompt_file: str = None
    ) -> Dict[str, Any]:
        """Assign topics to documents using TopicGPT"""
        try:
            print("Assigning topics to documents...")

            if not prompt_file:
                prompt_file = os.path.join(output_dir, "prompt_assign.txt")
                if not os.path.exists(prompt_file):
                    return {
                        "error": f"Prompt file not found: {prompt_file}. Please provide a prompt file for topic assignment."
                    }

            # Set up TopicGPT parameters
            data_file = os.path.join(output_dir, "data.jsonl")
            out_file = os.path.join(output_dir, "assignments.jsonl")

            # Assign topics
            assign_topics(
                api=self.api,
                model=self.topicgpt_model,
                data=data_file,
                prompt_file=prompt_file,
                out_file=out_file,
                topic_file=topic_file,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                verbose=True,
            )

            # Load assignments
            assignments = []
            with open(out_file, "r", encoding="utf-8") as f:
                for line in f:
                    assignments.append(json.loads(line))

            print(f"Assigned topics to {len(assignments)} documents")
            return {
                "assignments": assignments,
                "assignment_file": out_file,
                "success": True,
            }

        except Exception as e:
            traceback.print_exc()
            print(f"Error assigning topics: {str(e)}")
            return {"error": str(e), "success": False}

    def correct_topic_assignments(
        self, topic_file: str, output_dir: str, prompt_file: str = None
    ) -> Dict[str, Any]:
        """Correct topic assignments using TopicGPT"""
        try:
            print("Correcting topic assignments...")

            if not prompt_file:
                prompt_file = os.path.join(output_dir, "prompt_correct.txt")
                if not os.path.exists(prompt_file):
                    return {
                        "error": f"Prompt file not found: {prompt_file}. Please provide a prompt file for assignment correction."
                    }

            # Set up TopicGPT parameters
            data_file = os.path.join(output_dir, "assignments.jsonl")
            out_file = os.path.join(output_dir, "corrected_assignments.jsonl")

            # Correct assignments
            correct_topics(
                api=self.api,
                model=self.topicgpt_model,
                data_path=data_file,
                prompt_path=prompt_file,
                topic_path=topic_file,
                output_path=out_file,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                verbose=True,
            )

            # Load corrected assignments
            with open(out_file, "r", encoding="utf-8") as f:
                assignments = []
                for line in f:
                    assignments.append(json.loads(line))

            print(f"Corrected assignments for {len(assignments)} documents")
            return {
                "assignments": assignments,
                "assignment_file": out_file,
                "success": True,
            }

        except Exception as e:
            traceback.print_exc()
            print(f"Error correcting assignments: {str(e)}")
            return {"error": str(e), "success": False}

    def process_single_pdf(self, pdf_file: Path) -> tuple[str, Dict[str, Any]]:
        """Process a single PDF file and return text"""
        try:
            print(f"Processing: {pdf_file.name}")
            text = self.pdf_processor.extract_text_from_pdf(str(pdf_file))

            if text.startswith("Error"):
                print(f"  Error: {text}")
                return pdf_file.name, {"error": text, "text": ""}

            return pdf_file.name, {
                "text": text,
                "text_length": len(text),
                "filename": pdf_file.name,
            }

        except Exception as e:
            error_msg = f"Error processing {pdf_file.name}: {str(e)}"
            print(f"  {error_msg}")
            return pdf_file.name, {"error": error_msg, "text": ""}

    def prepare_data(self, pdf_folder: str, output_dir: str, max_workers: int = 4) -> str:
        """Prepare data.jsonl from PDFs and return path to temp file"""
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        
        pdf_folder = Path(pdf_folder)
        pdf_files = list(pdf_folder.glob("*.pdf"))

        if not pdf_files:
            raise ValueError("No PDF files found")

        print(f"Extracting text from {len(pdf_files)} PDFs...")

        pdf_texts = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_pdf = {
                executor.submit(self.process_single_pdf, pdf_file): pdf_file
                for pdf_file in pdf_files
            }

            for future in as_completed(future_to_pdf):
                pdf_file = future_to_pdf[future]
                try:
                    pdf_name, pdf_data = future.result()
                    if "error" not in pdf_data:
                        pdf_texts.append(pdf_data)
                except Exception as e:
                    print(f"Error processing {pdf_file.name}: {str(e)}")

        # Save extracted data to temp file
        data_content = self.prepare_data_for_topicgpt(pdf_texts)
        data_file = output_dir / "data.jsonl"
        with open(data_file, "w", encoding="utf-8") as f:
            f.write(data_content)

        print(f"Prepared data.jsonl with {len(pdf_texts)} documents")
        return str(data_file)

    def execute_step(
        self, step: str, pdf_folder: str, output_dir: str, **kwargs
    ) -> Dict[str, Any]:
        """Execute a specific topic modeling step"""
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)

        # Prepare data.jsonl if needed for the step
        if step in ["level1", "level2", "assign", "correct"]:
            data_file = output_dir / "data.jsonl"
            if not data_file.exists():
                print("Data file not found, preparing data.jsonl...")
                self.prepare_data(pdf_folder, output_dir, kwargs.get("max_workers", 4))

        if step == "level1":
            return self._generate_level1_topics(
                output_dir, kwargs.get("prompt_file"), kwargs.get("seed_file")
            )
        elif step == "level2":
            return self._generate_level2_topics(
                output_dir, kwargs.get("level1_topics_file"), kwargs.get("prompt_file"), kwargs.get("seed_file")
            )
        elif step == "refine":
            return self._refine_topics(
                output_dir, kwargs.get("topic_file"), kwargs.get("prompt_file")
            )
        elif step == "assign":
            return self._assign_topics(
                output_dir, kwargs.get("topic_file"), kwargs.get("prompt_file")
            )
        elif step == "correct":
            return self._correct_assignments(
                output_dir, kwargs.get("topic_file"), kwargs.get("prompt_file")
            )
        else:
            raise ValueError(f"Unknown step: {step}")


    def _generate_level1_topics(
        self, output_dir: Path, prompt_file: str = None, seed_file: str = None
    ) -> Dict[str, Any]:
        """Generate level 1 topics"""
        return self.generate_topics_level1(str(output_dir), prompt_file, seed_file)

    def _generate_level2_topics(
        self, output_dir: Path, level1_topics_file: str = None, prompt_file: str = None, seed_file: str = None
    ) -> Dict[str, Any]:
        """Generate level 2 topics"""
        if not level1_topics_file:
            level1_topics_file = output_dir / "topics_lvl1.md"
            if not Path(level1_topics_file).exists():
                return {
                    "error": "Level 1 topics file not found. Run 'level1' step first."
                }

        with open(level1_topics_file, "r", encoding="utf-8") as f:
            topic_text = f.read()
            level1_topics = parse_topic_text(topic_text)

        return self.generate_topics_level2(level1_topics, str(output_dir), prompt_file, seed_file)

    def _refine_topics(
        self, output_dir: Path, topic_file: str = None, prompt_file: str = None
    ) -> Dict[str, Any]:
        """Refine topics"""
        if not topic_file:
            topic_file = output_dir / "topics_lvl1.md"
            if not Path(topic_file).exists():
                return {"error": "Topics file not found. Run 'level1' step first."}

        return self.refine_generated_topics(
            str(topic_file), str(output_dir), prompt_file
        )

    def _assign_topics(
        self, output_dir: Path, topic_file: str = None, prompt_file: str = None
    ) -> Dict[str, Any]:
        """Assign topics to documents"""
        if not topic_file:
            # Try refined topics first, then level1, then level2
            for file_name in [
                "topics_refined.md",
                "topics_lvl1.md",
                "topics_lvl2.md",
            ]:
                candidate_file = output_dir / file_name
                if candidate_file.exists():
                    topic_file = str(candidate_file)
                    break

            if not topic_file:
                return {
                    "error": "No topics file found. Run 'level1' or 'refine' step first."
                }

        return self.assign_topics_to_documents(topic_file, str(output_dir), prompt_file)

    def _correct_assignments(
        self, output_dir: Path, topic_file: str = None, prompt_file: str = None
    ) -> Dict[str, Any]:
        """Correct topic assignments"""
        if not topic_file:
            # Try refined topics first, then level1, then level2
            for file_name in [
                "topics_refined.md",
                "topics_lvl1.md",
                "topics_lvl2.md",
            ]:
                candidate_file = output_dir / file_name
                if candidate_file.exists():
                    topic_file = str(candidate_file)
                    break

            if not topic_file:
                return {
                    "error": "No topics file found. Run 'level1' or 'refine' step first."
                }

        return self.correct_topic_assignments(topic_file, str(output_dir), prompt_file)


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

2. level2    - Generate detailed sub-topics based on level1 topics
   Arguments: --prompt-file (optional), --seed-file (optional)

3. refine    - Merge similar topics and remove irrelevant ones
   Arguments: --prompt-file (optional)

4. assign    - Assign topics to individual documents
   Arguments: --topic-file (required), --prompt-file (optional)

5. correct   - Correct and improve topic assignments
   Arguments: --topic-file (required), --prompt-file (optional)

Each step must be run in order, and data extraction is done automatically when needed.

USAGE
-----
python topic_modeling_system.py <pdf_folder> --output-dir <output_dir> --step <step> [options]

REQUIRED ARGUMENTS
------------------
pdf_folder          Folder containing PDF files to process
--output-dir        Output directory to save results and intermediate files
--step              Specific step to execute (level1, level2, refine, assign, correct)

OPTIONAL ARGUMENTS
------------------
--config            Configuration file for LLM settings (default: llm_config.json)
--provider          LLM provider: openai, gemini, anthropic, openai-completion (default: openai)
--context-length    Maximum context length in tokens (default: 16385)
--max-workers       Number of parallel workers for PDF processing (default: 4)
--prompt-file       Path to prompt file for the current step
--seed-file         Path to seed file for level1 or level2 topic generation

REQUIRED ARGUMENTS (for specific steps)
---------------------------------------
--topic-file        Path to topics file (required for assign and correct steps)

EXAMPLES
--------
# Generate high-level topics
python topic_modeling_system.py test_pdfs --output-dir output-topicgpt --step level1 \\
    --prompt-file prompts/topicgpt/generationlvl1.txt

# Generate high-level topics with seed
python topic_modeling_system.py test_pdfs --output-dir output-topicgpt --step level1 \\
    --prompt-file prompts/topicgpt/generationlvl1.txt --seed-file prompts/topicgpt/seed1.md

# Generate detailed sub-topics
python topic_modeling_system.py test_pdfs --output-dir output-topicgpt --step level2 \\
    --prompt-file prompts/topicgpt/generationlvl2.txt --seed-file prompts/topicgpt/seed2.md

# Refine topics
python topic_modeling_system.py test_pdfs --output-dir output-topicgpt --step refine \\
    --prompt-file prompts/topicgpt/refinement.txt

# Assign topics to documents
python topic_modeling_system.py test_pdfs --output-dir output-topicgpt --step assign \\
    --topic-file output-topicgpt/topics_refined.md --prompt-file prompts/topicgpt/assignment.txt

# Correct topic assignments
python topic_modeling_system.py test_pdfs --output-dir output-topicgpt --step correct \\
    --topic-file output-topicgpt/topics_refined.md --prompt-file prompts/topicgpt/assignment.txt

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


def main():
    parser = argparse.ArgumentParser(
        description="PDF Topic Modeling System with TopicGPT and LangChain - Single Step Execution (Data extraction is automatic)"
    )
    parser.add_argument(
        "pdf_folder",
        help="Folder containing PDF files to process",
        default=analysis_conf["articles_folder"]
    )
    #FIXME: Check what this is and possible default value
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output directory to save results and intermediate files",
        default=analysis_conf["output_path"]
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
    parser.add_argument(
        "--context-length",
        type=int,
        default=16385,
        help="Maximum context length in tokens (default: 16385)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum number of parallel workers for PDF processing (default: 4)",
    )
    parser.add_argument(
        "--step",
        choices=["level1", "level2", "refine", "assign", "correct"],
        required=True,
        help="Specific step to execute. Steps must be run in order: level1 -> level2 -> refine -> assign -> correct. Data extraction is done automatically when needed.",
    )
    parser.add_argument(
        "--level1-topics-file",
        help="Path to level1 topics file (for level2 step)",
        default=f"{analysis_conf['output_path']}/topics_lvl1.md"
    )
    parser.add_argument(
        "--topic-file",
        help="Path to topics file (for refine, assign, correct steps)",
        default=f"{analysis_conf['output_path']}/{analysis_conf['topics_file']}"
    )
    parser.add_argument(
        "--seed-file",
        help="Path to seed file for level1 or level2 topic generation",
        default=f"{analysis_conf['output_path']}/{analysis_conf['seed_file']}"
    )
    parser.add_argument(
        "--help-detailed",
        action="store_true",
        help="Show detailed help with examples and workflow",
    )

    args = parser.parse_args()

    if args.help_detailed:
        show_detailed_help()
        return

    if not TOPICGPT_AVAILABLE:
        print("ERROR: TopicGPT is required but not installed.")
        print("Install with: pip install topicgpt_python")
        return

    # Load configuration
    if not os.path.exists(args.config):
        print(f"Configuration file not found: {args.config}")
        print("Please create a configuration file with your API keys.")
        return

    config = load_config(args.config)

    # Create LLM
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

    # Create topic modeling system
    topic_system = TopicModelingSystem(
        llm,
        use_chat_model,
        context_length,
        max_output_tokens,
        args.provider,
        provider_config["model"],
        provider_config.get("temperature", 0.7),
    )

    # Execute single step
    print(f"Executing step: {args.step}")
    result = topic_system.execute_step(
        args.step,
        args.pdf_folder,
        args.output_dir,
        max_workers=args.max_workers,
        level1_topics_file=args.level1_topics_file,
        topic_file=args.topic_file,
        prompt_file=f"prompts/{args.step}.txt",
        seed_file=args.seed_file,
    )

    if result.get("success", False):
        print(f"Step '{args.step}' completed successfully!")
        if "message" in result:
            print(result["message"])
    else:
        print(f"Step '{args.step}' failed: {result.get('error', 'Unknown error')}")
        return


if __name__ == "__main__":
    main()
