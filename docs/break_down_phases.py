#!/usr/bin/env python3
"""Script to break down large phase documents into smaller, manageable files."""

import re
from pathlib import Path


def extract_header_section(file_path: Path) -> str:
    """Extract header section (up to ## Implementation Plan)."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    header_lines = []
    for i, line in enumerate(lines):
        if line.strip() == "## Implementation Plan":
            header_lines.append(line)
            break
        header_lines.append(line)
    
    return ''.join(header_lines)

def find_implementation_sections(file_path: Path) -> list:
    """Find all implementation sections with their line ranges."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    sections = []
    current_section = None
    impl_plan_started = False
    
    for i, line in enumerate(lines):
        # Start tracking after Implementation Plan
        if line.strip() == "## Implementation Plan":
            impl_plan_started = True
            continue
            
        if not impl_plan_started:
            continue
            
        # Look for implementation steps (### 1. Title {#id})
        match = re.match(r'^### (\d+)\.\s+(.+?)\s+\{#([^}]+)\}', line.strip())
        if match:
            # Save previous section
            if current_section:
                current_section['end_line'] = i
                sections.append(current_section)
            
            # Start new section
            current_section = {
                'number': match.group(1),
                'title': match.group(2),
                'id': match.group(3),
                'start_line': i,
                'end_line': None
            }
    
    # Handle last section
    if current_section:
        current_section['end_line'] = len(lines)
        sections.append(current_section)
    
    return sections

def extract_section_content(file_path: Path, start_line: int, end_line: int) -> str:
    """Extract content between line ranges."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    return ''.join(lines[start_line:end_line])

def create_small_file(base_name: str, header: str, section: dict, section_content: str, output_dir: Path):
    """Create a small file with header + single implementation section."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{base_name}-impl-{section['number']}-{section['id']}.md"
    file_path = output_dir / filename
    
    content = header + "\n" + section_content
    
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f"Created: {filename} ({len(content.splitlines())} lines)")
    return file_path

def process_phase_file(file_path: Path):
    """Process a single phase file."""
    print(f"\nProcessing {file_path.name}...")
    
    # Extract header
    header = extract_header_section(file_path)
    print(f"Header extracted: {len(header.splitlines())} lines")
    
    # Find implementation sections
    sections = find_implementation_sections(file_path)
    print(f"Found {len(sections)} implementation sections")
    
    # Create output directory
    base_name = file_path.stem
    output_dir = Path(f"phase-breakdown/{base_name}")
    
    # Create small files
    created_files = []
    for section in sections:
        section_content = extract_section_content(
            file_path, section['start_line'], section['end_line']
        )
        
        small_file = create_small_file(
            base_name, header, section, section_content, output_dir
        )
        created_files.append(small_file)
    
    return created_files

def main():
    """Main function."""
    # Find all phase detail files
    phase_files = list(Path('.').glob('phase*-details.md'))
    
    if not phase_files:
        print("No phase detail files found!")
        return
    
    print(f"Found {len(phase_files)} phase files to process:")
    for f in phase_files:
        print(f"  - {f.name} ({sum(1 for _ in open(f))} lines)")
    
    # Process each file
    all_created_files = []
    for phase_file in phase_files:
        try:
            created_files = process_phase_file(phase_file)
            all_created_files.extend(created_files)
        except Exception as e:
            print(f"Error processing {phase_file}: {e}")
    
    print(f"\n✅ Successfully created {len(all_created_files)} smaller files")
    print("Files are organized in phase-breakdown/ directory")

if __name__ == "__main__":
    main()