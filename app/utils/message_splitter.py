"""
Message splitting utility for ManyChat's 2000-character limit
"""
import re
from typing import List

MANYCHAT_MAX_LENGTH = 2000

def split_message(message: str, max_length: int = MANYCHAT_MAX_LENGTH) -> List[str]:
    """
    Split a long message into chunks that fit ManyChat's character limit.
    
    Tries to split at natural boundaries (paragraphs, sentences, etc.) to maintain readability.
    
    Args:
        message: The message to split
        max_length: Maximum length per chunk (default: 2000 for ManyChat)
        
    Returns:
        List of message chunks, each under max_length characters
    """
    if len(message) <= max_length:
        return [message]
    
    chunks = []
    remaining = message
    
    while len(remaining) > max_length:
        # Try to find a good split point within the limit
        split_point = max_length
        
        # Look for paragraph breaks first (double newline)
        chunk_text = remaining[:max_length]
        paragraph_break = chunk_text.rfind('\n\n')
        if paragraph_break > max_length * 0.5:  # At least halfway through
            split_point = paragraph_break + 2  # Include the newlines
        else:
            # Look for single newline
            newline = chunk_text.rfind('\n')
            if newline > max_length * 0.5:
                split_point = newline + 1
            else:
                # Look for sentence end (. ! ?)
                sentence_end = max(
                    chunk_text.rfind('. '),
                    chunk_text.rfind('! '),
                    chunk_text.rfind('? ')
                )
                if sentence_end > max_length * 0.5:
                    split_point = sentence_end + 2  # Include the punctuation and space
                else:
                    # Look for comma or semicolon
                    comma = max(chunk_text.rfind(', '), chunk_text.rfind('; '))
                    if comma > max_length * 0.5:
                        split_point = comma + 2
                    else:
                        # Last resort: split at last space
                        space = chunk_text.rfind(' ')
                        if space > max_length * 0.7:  # Only if reasonably far in
                            split_point = space + 1
                        # Otherwise use max_length as-is
        
        # Extract the chunk and update remaining
        chunk = remaining[:split_point].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_point:].strip()
    
    # Add the last remaining piece
    if remaining:
        chunks.append(remaining)
    
    return chunks


def needs_splitting(message: str, max_length: int = MANYCHAT_MAX_LENGTH) -> bool:
    """Check if a message needs to be split"""
    return len(message) > max_length
