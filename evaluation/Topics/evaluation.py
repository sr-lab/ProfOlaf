import json
import os
import csv
import krippendorff
import numpy as np

possible_values = ["Automated Program Repair",
"Security Patch Detection",
"Code Summarization",
"Code Generation",
"Vulnerability Scoring",
"Code Vulnerability Analysis",
"Code Security Benchmarking",
"Large Language Models for Code Security",
"Benchmark Data Contamination Mitigation",
"Fault Localization",
"Code Optimization",
"Program Synthesis",
"LLM Reasoning and Rationale Generation",
"Cybersecurity Benchmarking for LLMs",
"Algorithmic Problem Solving",
"Code Understanding and Graph Extraction",
"Automated Code Review"]


def read_jsonl_file(file_path):
    """
    Read and parse JSON or JSONL files.
    For JSON files: returns a single JSON object
    For JSONL files: returns a list of JSON objects (one per line)
    """
    data = []
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            if line: 
                data.append(json.loads(line))
    
    if len(data) == 1 and not file_path.endswith('.jsonl'):
        return data[0]
    return data

def read_csv_file(file_path):
    with open(file_path, 'r') as file:
        reader = csv.reader(file)
        return list(reader)

def calculate_precision_recall(predicted, ground_truth):
    """
    Calculate precision and recall for a single document.
    
    Args:
        predicted: List of predicted topic names
        ground_truth: List of ground truth topic names
    
    Returns:
        tuple: (precision, recall, f1_score)
    """

    

    if not predicted and not ground_truth:
        return 1.0, 1.0, 1.0  # Perfect match when both are empty
    
    if not predicted:
        return 0.0, 0.0, 0.0  # No predictions, no precision/recall
    
    if not ground_truth:
        return 0.0, 1.0, 0.0  # No ground truth, precision=0, recall=1
    
    # Convert to sets for easier comparison
    predicted_set = set([pred.lower().strip() for pred in predicted])
    ground_truth_set = set([truth.lower().strip() for truth in ground_truth])

    #print(predicted_set, ground_truth_set)
    
    # Calculate true positives, false positives, false negatives
    true_positives = len(predicted_set.intersection(ground_truth_set))
    false_positives = len(predicted_set - ground_truth_set)
    false_negatives = len(ground_truth_set - predicted_set)
    
    # Calculate precision and recall
    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0.0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0.0
    
    # Calculate F1 score
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return precision, recall, f1_score

def evaluate_data(topicgpt_data, rater_data):
    """
    Evaluate the performance of topicgpt predictions against ground truth.
    
    Args:
        topicgpt_data: Dict with filename as key and list of predicted topics as value
        rater_data: Dict with filename as key and list of ground truth topics as value
    
    Returns:
        dict: Evaluation metrics
    """
    results = {
        'individual_scores': {},
        'overall_metrics': {}
    }
    
    total_precision = 0.0
    total_recall = 0.0
    total_f1 = 0.0
    valid_documents = 0
    
    # Get all common filenames
    topicgpt_data = {k.lower(): v for k, v in topicgpt_data.items()}
    rater_data = {k.lower(): v for k, v in rater_data.items()}
    common_files = set(topicgpt_data.keys()).intersection(set(rater_data.keys()))
    # Show not common files
    not_common_files = set(topicgpt_data.keys()).difference(set(rater_data.keys()))
    #print(f"TopicGPT but not rater: {not_common_files}")
    not_common_files = set(rater_data.keys()).difference(set(topicgpt_data.keys()))
    #print(f"Rater but not TopicGPT: {not_common_files}")

    print(f"Evaluating {len(common_files)} documents...")

    all_predictions = []
    all_ground_truths = []
    for filename in common_files:
        all_predictions.append([el.lower().strip() for el in topicgpt_data[filename]])
        all_ground_truths.append([el.lower().strip() for el in rater_data[filename]])
    krippendorff_alpha = calculate_krippendorff_alpha_multiple(all_predictions, all_ground_truths, possible_values)
    print(f"Krippendorff's alpha: {krippendorff_alpha}")

    for filename in common_files:
        predicted = topicgpt_data[filename]
        ground_truth = rater_data[filename]
        
        precision, recall, f1 = calculate_precision_recall(predicted, ground_truth)
        
        results['individual_scores'][filename] = {
            'precision': precision,
            'recall': recall,
            'f1_score': f1,
            'predicted_topics': predicted,
            'ground_truth_topics': ground_truth
        }
        
        total_precision += precision
        total_recall += recall
        total_f1 += f1
        valid_documents += 1
        
        #print(f"{filename}: P={precision:.3f}, R={recall:.3f}, F1={f1:.3f}")
    
    # Calculate overall metrics
    if valid_documents > 0:
        results['overall_metrics'] = {
            'average_precision': total_precision / valid_documents,
            'average_recall': total_recall / valid_documents,
            'average_f1': total_f1 / valid_documents,
            'total_documents': valid_documents
        }
    
    return results

def calculate_krippendorff_alpha_multiple(all_predictions, all_ground_truths, possible_values):
    """
    Calculate Krippendorff's alpha for multi-label classification.
    Each article can have multiple category labels.
    """
    n_articles = len(all_predictions)
    n_categories = len(possible_values)
    
    # Create binary matrix: rows=raters, cols=article-category pairs
    # Shape: (2 raters, n_articles * n_categories)
    rater1_data = []
    rater2_data = []
    
    for pred, truth in zip(all_predictions, all_ground_truths):
        # Normalize for comparison
        pred_normalized = [cat.lower().strip() for cat in pred]
        truth_normalized = [cat.lower().strip() for cat in truth]
        
        # For each possible category, mark if it's present
        for cat in possible_values:
            cat_norm = cat.lower().strip()
            rater1_data.append(1 if cat_norm in pred_normalized else 0)
            rater2_data.append(1 if cat_norm in truth_normalized else 0)
    
    data = np.array([rater1_data, rater2_data])
    return krippendorff.alpha(reliability_data=data, level_of_measurement='nominal')

def parse_response(response):
    """
    Parse response string to extract topic names.
    Expected format: [1] Topic Name: Description...
    Returns list of topic names.
    """
    import re
    
    lines = response.split('\n')
    topic_names = []
    for line in lines:
        line = line.strip()
        match = re.match(r'\[\d+\]\s+([^:]+):', line)
        if match:
            topic_name = match.group(1).strip()
            if topic_name:
                topic_names.append(topic_name)
    
    return topic_names


def parse_topicgpt_data(data):
    parsed_data = {}
    for item in data:
        filename = item["filename"].replace(".pdf", "").replace(".pdf", "").replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace("*", "_").replace("?", "_").replace("\"", "_").replace("<", "_").replace(">", "_").replace(".", "_")
        
        response_parsed = parse_response(item["responses"])
        parsed_data[filename] = response_parsed
    return parsed_data

def parse_rater_data(data):
    parsed_data = {}
    for item in data:
        if item[0] == "":
            continue
        filename = item[1].replace(".pdf", "").replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace("*", "_").replace("?", "_").replace("\"", "_").replace("<", "_").replace(">", "_").replace(".", "_")
        response_parsed = item[4].split(",")
        parsed_data[filename] = response_parsed
    return parsed_data

# Load and parse data
topicgpt_data = read_jsonl_file('corrected_assignments.jsonl')
topicgpt_data = parse_topicgpt_data(topicgpt_data)
rater_data = read_csv_file('evaluation_agreement.csv')
rater_data = parse_rater_data(rater_data)

# Run evaluation
print("Running evaluation...")
evaluation_results = evaluate_data(topicgpt_data, rater_data)

# Print overall results
if evaluation_results['overall_metrics']:
    metrics = evaluation_results['overall_metrics']
    print(f"\n=== OVERALL RESULTS ===")
    print(f"Total documents evaluated: {metrics['total_documents']}")
    print(f"Average Precision: {metrics['average_precision']:.3f}")
    print(f"Average Recall: {metrics['average_recall']:.3f}")
    print(f"Average F1 Score: {metrics['average_f1']:.3f}")