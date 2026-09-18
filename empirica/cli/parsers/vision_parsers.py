"""Vision command parsers."""


def add_vision_parsers(subparsers):
    """Add vision command parsers"""
    # Vision command
    vision_parser = subparsers.add_parser(
        "vision", help="Image metadata assessment (PIL, no models) for one image or a deck"
    )
    vision_parser.add_argument("image", nargs="?", help="Path to one image file")
    vision_parser.add_argument("--pattern", help="Glob of slide images to assess as a deck (instead of one image)")
    vision_parser.add_argument("--session-id", help="Log one finding per image to this session")
    vision_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
