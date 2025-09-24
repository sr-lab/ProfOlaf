# TopicGPT imports
import os
import json
import traceback
import argparse
import re
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from abc import ABC, abstractmethod

from shared_utils import (
    PDFProcessor,
    load_config,
    create_llm,
    get_use_chat_model,
    truncate_text,
)

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


class TopicModelingStep(ABC):
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
    
    def parse_topic_text(self, topic_text: str) -> List[Dict[str, Any]]:
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

        
    @abstractmethod
    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        pass

class TopicModelingLevel1(TopicModelingStep):
    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        data_file = output_dir / "data.jsonl"
        if not data_file.exists():
            print("Data file not found, preparing data.jsonl...")
            self.prepare_data_for_topicgpt(pdf_folder, output_dir, kwargs.get("max_workers", 4))

        seed_file = kwargs.get("seed_file", "")
        prompt_file = kwargs.get("prompt_file", "")
        if not seed_file or not prompt_file:
            print("Seed file or prompt file not found, preparing...")
            return {}
        
        try:
            print("Generating high-level topics...")
            data_file = os.path.join(str(output_dir), "data.jsonl")
            topic_file = os.path.join(str(output_dir), "topics_lvl1.md")
            out_file = os.path.join(str(output_dir), "generation_lvl1.json")

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
                topics = self.parse_topic_text(topic_text)

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

class TopicModelingLevel2(TopicModelingStep):
    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        data_file = output_dir / "data.jsonl"
        if not data_file.exists():
            print("Data file not found, preparing data.jsonl...")
            self.prepare_data_for_topicgpt(pdf_folder, output_dir, kwargs.get("max_workers", 4))

        level1_topics_file = kwargs.get("level1_topics_file", "")
        prompt_file = kwargs.get("prompt_file", "")
        seed_file = kwargs.get("seed_file", "")
        if not seed_file or not prompt_file or not level1_topics_file:
            print("Seed file or prompt file or level1 topics file not found, preparing...")
            return {}
        try:
            print("Generating low-level topics...")
            data_file = os.path.join(str(output_dir), "generation_lvl1.json")
            topic_file = os.path.join(str(output_dir), "topics_lvl2.md")
            out_file = os.path.join(str(output_dir), "generation_lvl2.json")
            
            # Generate topics
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
            
            with open(topic_file, "r", encoding="utf-8") as f:
                topic_text = f.read()
                topics = self.parse_topic_text(topic_text)

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

class TopicModelingRefine(TopicModelingStep):
    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)
        data_file = output_dir / "data.jsonl"
        if not data_file.exists():
            print("Data file not found, preparing data.jsonl...")
            self.prepare_data_for_topicgpt(pdf_folder, output_dir, kwargs.get("max_workers", 4))

        topic_file = kwargs.get("topic_file", "")
        prompt_file = kwargs.get("prompt_file", "")
        if not topic_file or not prompt_file:
            print("Topic file or prompt file not found, preparing...")
            return {}
        try:
            print("Refining topics...")

            if not prompt_file:
                prompt_file = os.path.join(output_dir, "prompt_refine.txt")
                if not os.path.exists(prompt_file):
                    return {
                        "error": f"Prompt file not found: {prompt_file}. Please provide a prompt file for topic refinement."
                    }
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
                topics = self.parse_topic_text(topic_text)

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
        
class TopicModelingAssign(TopicModelingStep):
    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        data_file = output_dir / "data.jsonl"
        if not data_file.exists():
            print("Data file not found, preparing data.jsonl...")
            self.prepare_data(pdf_folder, output_dir, kwargs.get("max_workers", 4))
        
        prompt_file = kwargs.get("prompt_file", "")
        topic_file = kwargs.get("topic_file", "")

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

class TopicModelingCorrect(TopicModelingStep):
    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        data_file = output_dir / "data.jsonl"
        if not data_file.exists():
            print("Data file not found, preparing data.jsonl...")
            self.prepare_data(pdf_folder, output_dir, kwargs.get("max_workers", 4))
        topic_file = kwargs.get("topic_file", "")
        prompt_file = kwargs.get("prompt_file", "")
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
            
class TopicModelingSystem:
    def __init__(self, topic_modeling_step: TopicModelingStep):
        self.topic_modeling_step = topic_modeling_step

    def execute_step(self, pdf_folder: str, output_dir: str, **kwargs) -> Dict[str, Any]:
        return self.topic_modeling_step.execute_step(pdf_folder, output_dir, **kwargs)
    