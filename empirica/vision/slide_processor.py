"""
Epistemic Slide Processor - Vision + Epistemic Tracking

Processes slide images and assesses their epistemic value:
- Extracts text via OCR
- Detects visual elements (diagrams, code, tables)
- Rates epistemic quality (clarity, signal, density, impact)
- Generates context summaries for project-bootstrap
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import cv2  # pyright: ignore[reportMissingImports]
    import numpy as np
    import pytesseract  # pyright: ignore[reportMissingImports]
    from PIL import Image  # pyright: ignore[reportMissingImports]
    HAS_VISION = True
except ImportError:
    HAS_VISION = False


@dataclass
class SlideEpistemicAssessment:
    """Epistemic quality assessment of a slide"""
    slide_path: str
    slide_number: int

    # Extracted content
    text_content: str
    has_diagram: bool
    has_code: bool
    has_table: bool
    word_count: int

    # Epistemic vectors (0.0-1.0)
    clarity: float  # How clear is the visual presentation?
    signal: float   # Signal-to-noise ratio of information
    density: float  # Information density (inverted - high = harder to parse)
    impact: float   # How much does this increase understanding?

    # Context quality
    context_value: float  # Overall value for project context (0.0-1.0)
    summary: str  # 2-3 sentence summary
    key_concepts: list[str]  # Extracted key concepts

    def to_dict(self) -> dict:
        """Convert assessment to dictionary representation."""
        d = asdict(self)
        # Convert numpy bools to Python bools for JSON serialization
        for k, v in d.items():
            if isinstance(v, np.bool_):
                d[k] = bool(v)
        return d


class SlideProcessor:
    """Process slides and assess epistemic value"""

    def __init__(self, output_dir: Path | None = None) -> None:
        """Initialize slide processor with output directory."""
        if not HAS_VISION:
            raise ImportError(
                "Vision dependencies not installed. "
                "Run: pip install pytesseract pillow opencv-contrib-python"
            )

        self.output_dir = output_dir or Path(".empirica/slides")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_slide(self, slide_path: Path, slide_number: int) -> SlideEpistemicAssessment:
        """Process a single slide and assess epistemic value"""

        # Load image
        img = Image.open(slide_path)
        img_cv = cv2.imread(str(slide_path))

        # Extract text via OCR
        text_content = str(pytesseract.image_to_string(img))
        word_count = len(text_content.split())

        # Detect visual elements
        has_diagram = self._detect_diagram(img_cv)
        has_code = self._detect_code(text_content)
        has_table = self._detect_table(img_cv, text_content)

        # Assess epistemic vectors
        clarity = self._assess_clarity(img_cv, text_content, has_diagram)
        signal = self._assess_signal(text_content, word_count, has_diagram)
        density = self._assess_density(word_count, img.size, has_diagram)
        impact = self._assess_impact(text_content, has_diagram, has_code)

        # Generate summary and extract concepts
        key_concepts = self._extract_key_concepts(text_content)
        summary = self._generate_summary(text_content, key_concepts, slide_number)

        # Calculate overall context value
        context_value = self._calculate_context_value(
            clarity, signal, density, impact, word_count
        )

        return SlideEpistemicAssessment(
            slide_path=str(slide_path),
            slide_number=slide_number,
            text_content=text_content,
            has_diagram=has_diagram,
            has_code=has_code,
            has_table=has_table,
            word_count=word_count,
            clarity=clarity,
            signal=signal,
            density=density,
            impact=impact,
            context_value=context_value,
            summary=summary,
            key_concepts=key_concepts,
        )

    def _detect_diagram(self, img_cv: np.ndarray) -> bool:
        """Detect if slide contains diagrams/visual elements"""
        # Convert to grayscale
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

        # Edge detection
        edges = cv2.Canny(gray, 50, 150)

        # Count edge pixels
        edge_ratio = np.sum(edges > 0) / edges.size

        # Heuristic: >5% edge pixels suggests diagram
        return bool(edge_ratio > 0.05)

    def _detect_code(self, text: str) -> bool:
        """Detect if slide contains code snippets"""
        code_indicators = [
            'def ', 'class ', 'import ', 'function',
            '{', '}', '()', '=>', 'return',
            'const ', 'let ', 'var ', 'async'
        ]
        return any(indicator in text for indicator in code_indicators)

    def _detect_table(self, img_cv: np.ndarray, text: str) -> bool:
        """Detect if slide contains tables"""
        # Check for aligned text patterns (simple heuristic)
        lines = text.split('\n')
        aligned_lines = sum(1 for line in lines if line.count('|') >= 2)

        # Or detect horizontal/vertical lines in image
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)

        # Detect horizontal lines
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        horizontal_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, horizontal_kernel)

        h_line_ratio = np.sum(horizontal_lines > 0) / horizontal_lines.size

        return bool(aligned_lines > 3 or h_line_ratio > 0.01)

    def _assess_clarity(self, img_cv: np.ndarray, text: str, has_diagram: bool) -> float:
        """Assess visual clarity (0.0-1.0)"""
        # Factors: contrast, text-to-image ratio, whitespace

        # 1. Image contrast
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        contrast = gray.std() / 128.0  # Normalized

        # 2. Text length (not too dense)
        text_score = 1.0 - min(len(text) / 2000, 1.0)

        # 3. Has clear visual elements
        visual_score = 1.0 if has_diagram else 0.7

        return min((contrast * 0.3 + text_score * 0.4 + visual_score * 0.3), 1.0)

    def _assess_signal(self, text: str, word_count: int, has_diagram: bool) -> float:
        """Assess signal-to-noise ratio (0.0-1.0)"""
        if word_count == 0:
            return 0.3 if has_diagram else 0.0

        # Keywords indicating high-signal content
        high_signal_terms = [
            'architecture', 'framework', 'epistemic', 'cascade',
            'workflow', 'system', 'component', 'assessment',
            'uncertainty', 'calibration', 'mirror', 'principle'
        ]

        signal_words = sum(1 for term in high_signal_terms if term.lower() in text.lower())
        signal_ratio = signal_words / max(word_count / 100, 1)

        # Bonus for diagrams (visual signal)
        diagram_bonus = 0.2 if has_diagram else 0.0

        return min(signal_ratio + diagram_bonus, 1.0)

    def _assess_density(self, word_count: int, img_size: tuple[int, int],
                       has_diagram: bool) -> float:
        """Assess information density (0.0-1.0, higher = harder to parse)"""
        img_area = img_size[0] * img_size[1]

        # Words per pixel (normalized)
        density = (word_count / img_area) * 1000000

        # Diagrams reduce perceived density
        if has_diagram:
            density *= 0.7

        return min(density, 1.0)

    def _assess_impact(self, text: str, has_diagram: bool, has_code: bool) -> float:
        """Assess potential impact on understanding (0.0-1.0)"""
        # High-impact indicators
        impact_terms = [
            'key', 'critical', 'core', 'essential', 'fundamental',
            'important', 'principle', 'foundation', 'goal'
        ]

        impact_score = sum(1 for term in impact_terms if term.lower() in text.lower()) / 10

        # Diagrams and code have high teaching impact
        if has_diagram:
            impact_score += 0.3
        if has_code:
            impact_score += 0.2

        return min(impact_score, 1.0)

    def _extract_key_concepts(self, text: str) -> list[str]:
        """Extract key concepts from text (simple heuristic)"""
        # Capitalize words likely to be concepts
        words = text.split()
        concepts = [
            word.strip('.,;:()[]{}')
            for word in words
            if word and word[0].isupper() and len(word) > 3
        ]

        # Deduplicate and return top 10
        return list(dict.fromkeys(concepts))[:10]

    def _generate_summary(self, text: str, concepts: list[str],
                         slide_number: int) -> str:
        """Generate summary (simple extraction)"""
        sentences = text.split('.')
        if not sentences:
            return f"Slide {slide_number}: Visual content"

        # Take first 2-3 meaningful sentences
        summary_sentences = [s.strip() for s in sentences if len(s.strip()) > 20][:3]

        if summary_sentences:
            return f"Slide {slide_number}: " + '. '.join(summary_sentences) + '.'
        else:
            return f"Slide {slide_number}: Covers {', '.join(concepts[:3])}."

    def _calculate_context_value(self, clarity: float, signal: float,
                                 density: float, impact: float,
                                 word_count: int) -> float:
        """Calculate overall context value for project-bootstrap"""
        # High clarity, high signal, low density, high impact = high value
        # Invert density (high density = harder to parse)

        value = (
            clarity * 0.25 +
            signal * 0.35 +
            (1.0 - density) * 0.15 +
            impact * 0.25
        )

        # Penalize empty slides
        if word_count < 10:
            value *= 0.3

        return min(value, 1.0)

    def process_slide_deck(self, slide_pattern: str) -> list[SlideEpistemicAssessment]:
        """Process entire slide deck and generate report"""
        slide_files = sorted(Path('.').glob(slide_pattern))

        if not slide_files:
            raise FileNotFoundError(f"No slides found matching: {slide_pattern}")

        assessments = []
        for idx, slide_path in enumerate(slide_files, start=1):
            print(f"Processing {slide_path.name}...")
            assessment = self.process_slide(slide_path, idx)
            assessments.append(assessment)

        # Save report
        self._save_report(assessments, slide_pattern)

        return assessments

    def _save_report(self, assessments: list[SlideEpistemicAssessment],
                    pattern: str):
        """Save epistemic assessment report"""
        report_path = self.output_dir / f"assessment_{pattern.replace('*', 'all')}.json"

        report = {
            "pattern": pattern,
            "total_slides": len(assessments),
            "avg_context_value": sum(a.context_value for a in assessments) / len(assessments),
            "avg_clarity": sum(a.clarity for a in assessments) / len(assessments),
            "avg_signal": sum(a.signal for a in assessments) / len(assessments),
            "slides": [a.to_dict() for a in assessments],
        }

        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"\n✓ Report saved to {report_path}")

        # Also save human-readable summary
        summary_path = self.output_dir / f"summary_{pattern.replace('*', 'all')}.md"
        self._save_summary(assessments, summary_path)

    def _save_summary(self, assessments: list[SlideEpistemicAssessment],
                     output_path: Path):
        """Save human-readable summary"""
        with open(output_path, 'w') as f:
            f.write("# Slide Deck Epistemic Assessment\n\n")

            # Overall stats
            avg_context = sum(a.context_value for a in assessments) / len(assessments)
            f.write(f"**Total Slides:** {len(assessments)}\n")
            f.write(f"**Average Context Value:** {avg_context:.2f}\n\n")

            # High-value slides
            high_value = [a for a in assessments if a.context_value > 0.7]
            if high_value:
                f.write("## High-Value Slides (Context > 0.7)\n\n")
                for a in high_value:
                    f.write(f"### Slide {a.slide_number} (Value: {a.context_value:.2f})\n")
                    f.write(f"- **Clarity:** {a.clarity:.2f} | **Signal:** {a.signal:.2f} | ")
                    f.write(f"**Density:** {a.density:.2f} | **Impact:** {a.impact:.2f}\n")
                    f.write(f"- **Summary:** {a.summary}\n")
                    f.write(f"- **Key Concepts:** {', '.join(a.key_concepts[:5])}\n\n")

            # All slides
            f.write("## All Slides\n\n")
            for a in assessments:
                f.write(f"**Slide {a.slide_number}** | ")
                f.write(f"Context: {a.context_value:.2f} | ")
                f.write(f"Words: {a.word_count} | ")
                f.write(f"Diagram: {'✓' if a.has_diagram else '✗'}\n")

        print(f"✓ Summary saved to {output_path}")


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Process slides with epistemic assessment")
    parser.add_argument("pattern", help="Slide file pattern (e.g., 'ledger-*.png')")
    parser.add_argument("--output-dir", default=".empirica/slides",
                       help="Output directory for reports")

    args = parser.parse_args()

    processor = SlideProcessor(output_dir=Path(args.output_dir))
    assessments = processor.process_slide_deck(args.pattern)

    print(f"\n✓ Processed {len(assessments)} slides")
    print(f"Average context value: {sum(a.context_value for a in assessments) / len(assessments):.2f}")


if __name__ == "__main__":
    main()
