
import logging
from typing import List

from agent.config import logger 

def parse_medicines_from_text(medicines_text: str) -> List[str]:
    """Parse medicine details from LLM response text - keep as complete strings"""
    try:
        medicines = []
        
        # Split by semicolon for multiple medicines
        medicine_parts = medicines_text.split(';')

        for part in medicine_parts:
            part = part.strip()
            if part and not part.lower().startswith(('medicines_found', 'appointment_found')):
                # Keep the complete medicine information as one string
                medicines.append(part)
        
        # If no medicines found using semicolon split, try line-by-line parsing
        if not medicines:
            lines = medicines_text.split('\n')
            for line in lines:
                line = line.strip()
                if line and not line.lower().startswith(('medicines', 'appointment')):
                    medicines.append(line)
        
        # If still no medicines, return the original text as single medicine
        return medicines if medicines else [medicines_text.strip()]
        
    except Exception as e:
        logger.error(f"Failed to parse medicines: {e}")
        return [medicines_text.strip()]