"""Post-process combined documents to fit S3 Vectors 2048-byte limit."""

from pathlib import Path
import re
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).parent / "output" / "documents"

def compress_combined_document(text: str) -> str:
    """Compress a combined document to under 2000 bytes."""
    lines = text.strip().splitlines()
    
    # Extract header
    geo_line = lines[0]
    code_line = lines[1] if len(lines) > 1 else ""
    
    # Extract CODE and LEVEL
    code_match = re.search(r'CODE: (\S+)', code_line)
    level_match = re.search(r'LEVEL: (\w+)', code_line)
    code = code_match.group(1) if code_match else "?"
    level = level_match.group(1) if level_match else "?"
    
    # Shorten geography name
    geo = geo_line.replace("GEOGRAPHY: ", "").replace("GEOGRAPHIC CODE:", "")
    if len(geo) > 50:
        geo = geo[:47] + "..."
    
    # Extract year lines
    year_data = {}
    for line in lines[3:]:  # Skip header and SOURCE
        if line.startswith("YEAR"):
            year_match = re.match(r'YEAR (\d{4}): (.+)', line)
            if year_match:
                year = year_match.group(1)
                data = year_match.group(2)
                year_data[year] = data
   
    # Extract key metrics from first available year
    if year_data:
        sample_data = list(year_data.values())[0]
        metrics = {}
        
        # Parse metrics
        for part in sample_data.split(";"):
            part = part.strip()
            if ": " in part:
                key, val = part.split(": ", 1)
                key = key.strip().lower()
                
                # Abbreviate metric names
                if "population" in key and "under" not in key and "age" not in key:
                    metrics["pop"] = {}
                elif "median household income" in key:
                    metrics["medinc"] = {}
                elif "poverty" in key:
                    metrics["pov"] = {}
                elif "homeownership" in key:
                    metrics["own"] = {}
                elif "unemployment" in key:
                    metrics["unemp"] = {}
        
        # Extract values for each metric across years
        for year, data in sorted(year_data.items()):
            for part in data.split(";"):
                part = part.strip()
                if ": " in part:
                    key, val = part.split(": ", 1)
                    key = key.strip().lower()
                    val = val.strip().rstrip(".")
                    
                    # Map to short keys
                    short_key = None
                    if "population" in key and "under" not in key and "age" not in key:
                        short_key = "pop"
                    elif "median household income" in key:
                        short_key = "medinc"
                    elif "poverty" in key:
                        short_key = "pov"
                    elif "homeownership" in key:
                        short_key = "own"
                    elif "unemployment" in key:
                        short_key = "unemp"
                    
                    if short_key and short_key in metrics:
                        # Compress value: remove commas and extra spaces
                        val = val.replace(",", "").replace(" ", "")
                        metrics[short_key][year] = val
        
        # Build compressed document
        compressed = [
            f"GEO: {geo}",
            f"CODE: {code} | LVL: {level}",
        ]
        
        for metric_key in ["pop", "medinc", "pov", "own", "unemp"]:
            if metric_key in metrics:
                values = [metrics[metric_key].get(y, "—") for y in sorted(year_data.keys())]
                compressed.append(f"{metric_key}: " + "|".join(values))
        
        return "\n".join(compressed)
    
    # Fallback: return original if parsing failed
    return text[:2000]


def main():
    # Find all combined documents (no year suffix)
    combined_docs = [
        p for p in OUTPUT_DIR.glob("*.txt")
        if not any(p.stem.endswith(f"_{y}") for y in [2016, 2017, 2018, 2019, 2020])
    ]
    
    print(f"Found {len(combined_docs)} combined documents")
    
    compressed_count = 0
    for doc_path in tqdm(combined_docs, desc="Compressing combined documents"):
        original = doc_path.read_text(encoding="utf-8")
        original_size = len(original.encode("utf-8"))
        
        if original_size > 2000:
            compressed = compress_combined_document(original)
            compressed_size = len(compressed.encode("utf-8"))
            
            if compressed_size < 2000:
                doc_path.write_text(compressed, encoding="utf-8")
                compressed_count += 1
    
    print(f"Compressed {compressed_count} documents to under 2KB")


if __name__ == "__main__":
    main()
