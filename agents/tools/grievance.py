"""
Grievance submission tool for PM-KISAN portal
Handles encrypted communication with the grievance service API
"""

import json
import os
import requests
from helpers.utils import get_logger
from typing import Dict, Any
from pydantic import BaseModel, Field
from helpers.encryption import hex_to_bytes, encrypt_aes_gcm, decrypt_aes_gcm

# Set up logger
logger = get_logger(__name__)

base_url = os.getenv("GRIEVANCE_BASE_URL")

# Load grievance type codes and descriptions from JSON file
current_dir = os.path.dirname(os.path.abspath(__file__))
assets_dir = os.path.join(current_dir, '..', '..', 'assets')
json_path = os.path.join(assets_dir, 'grievance_types.json')

with open(json_path, 'r', encoding='utf-8') as f:
    GRIEVANCE_TYPES = json.load(f)

# Load grievance keywords from JSON file
keywords_path = os.path.join(assets_dir, 'grievance_keywords.json')
with open(keywords_path, 'r', encoding='utf-8') as f:
    GRIEVANCE_KEYWORDS = json.load(f)


def _get_encryption_keys():
    """
    Get encryption keys from environment variables
    
    Returns:
        Tuple of (key_bytes, iv_bytes)
    """
    KEY_1 = os.getenv("GRIEVANCE_KEY_1")
    KEY_2 = os.getenv("GRIEVANCE_KEY_2")
    return hex_to_bytes(KEY_1), hex_to_bytes(KEY_2)


def _encrypt_and_send(request_data: dict, api_url: str) -> requests.Response:
    """
    Encrypt the request data and send POST request to the API
    
    Args:
        request_data: Dictionary of request data
        api_url: API endpoint URL
    
    Returns:
        Response object from the API
    """
    key_bytes, iv_bytes = _get_encryption_keys()
    
    json_request = json.dumps(request_data)
    encrypted_request = encrypt_aes_gcm(json_request, key_bytes, iv_bytes)
    
    encrypted_payload = {
        "EncryptedRequest": encrypted_request
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    response = requests.post(
        api_url,
        json=encrypted_payload,
        headers=headers,
        timeout=30
    )
    
    return response


def _decrypt_response(encrypted_response: str) -> dict:
    """
    Decrypt the encrypted response
    
    Args:
        encrypted_response: Encrypted response string
    
    Returns:
        Decrypted data as dictionary
    """
    key_bytes, iv_bytes = _get_encryption_keys()
    decrypted_response = decrypt_aes_gcm(encrypted_response, key_bytes, iv_bytes)
    decrypted_data = json.loads(decrypted_response)
    return decrypted_data


def _handle_identity_number(identity_no: str) -> tuple:
    """
    Handle identity number - determine if it's Aadhaar or registration number
    and get token if needed
    
    Args:
        identity_no: Identity number (Aadhaar or registration number)
    
    Returns:
        Tuple of (actual_identity_no, request_type, success_dict)
        If successful: (identity_no, request_type, None)
        If failed: (None, None, error_dict)
    """
    if identity_no.isdigit() and len(identity_no) == 12:
        aadhaar_token = get_aadhaar_token(identity_no)
        
        if aadhaar_token:
            return aadhaar_token, "IdentityNo", None
        else:
            logger.error(f"Failed to get Aadhaar token")
            error_dict = {
                "success": False,
                "error": "The provided Aadhaar number is not registered with PM-KISAN. Please provide either your Aadhaar number that is registered with PM-KISAN or your PM-KISAN registration number.",
                "status_code": 400
            }
            return None, None, error_dict
    else:
        return identity_no, "Reg_No", None


class GrievanceRequest(BaseModel):
    """Pydantic model for grievance submission validation"""
    identity_no: str = Field(..., min_length=1, description="Identity number")
    grievance_type: str = Field(..., description="Type of grievance")
    grievance_description: str = Field(..., min_length=10, description="Description of the grievance")
    
  
def get_aadhaar_token(identity_no: str) -> str:
    """
    Get Aadhaar token for 12-digit identity numbers
    
    Args:
        identity_no: 12-digit Aadhaar number
    
    Returns:
        Aadhaar token string or None if failed
    """
    request_data = {
        "Type": "IdentityNo_Details",
        "TokenNo": "PMK_123456",
        "IdentityNo": identity_no
    }
    
    token_url = f"{base_url}/GrievanceAadhaarToken"
    response = _encrypt_and_send(request_data, token_url)
    
    if response.status_code != 200:
        logger.error(f"Aadhaar token request failed with status {response.status_code}: {response.text}")
        return None
    
    response_data = response.json()
    if "d" not in response_data or "output" not in response_data["d"]:
        return None
    
    encrypted_response = response_data["d"]["output"]
    decrypted_data = _decrypt_response(encrypted_response)
    
    if "Responce" in decrypted_data and decrypted_data["Responce"] == "True":
        return decrypted_data.get("AadhaarToken")
    return None


def submit_grievance(
    identity_no: str,
    grievance_type: str,
    grievance_description: str,
    api_url: str = f"{base_url}/LodgeGrievance"
) -> Dict[str, Any]:
    """
    Submit a grievance to the PM-KISAN portal
    
    Args:
        identity_no: Identity number (e.g., "DL214294806")
        grievance_type: Type of grievance (e.g., "G001")
        grievance_description: Description of the grievance
        api_url: API endpoint URL
    
    Returns:
        Dictionary containing the response from the grievance service
    """
    actual_identity_no, request_type, error_dict = _handle_identity_number(identity_no)
    if error_dict:
        return error_dict
    
    if request_type == "IdentityNo":
        request_type = "IdentityNo_Details"
    elif request_type == "Reg_No":
        request_type = "Reg_No_Details"
    
    request_data = {
        "Type": request_type,
        "TokenNo": "PMK_123456",
        "IdentityNo": actual_identity_no,
        "GrievanceType": grievance_type,
        "GrievanceDescription": grievance_description
    }
    
    response = _encrypt_and_send(request_data, api_url)
    
    if response.status_code != 200:
        logger.error(f"Grievance submission failed with status {response.status_code}: {response.text}")
        return {
            "success": False,
            "error": f"HTTP {response.status_code}: {response.text}",
            "status_code": response.status_code
        }
    
    response_data = response.json()
    
    if "d" not in response_data or "output" not in response_data["d"]:
        logger.error("Invalid response format from grievance submission service")
        return {
            "success": False,
            "error": "Invalid response format from grievance service",
            "raw_response": response_data
        }
    
    encrypted_response = response_data["d"]["output"]
    decrypted_data = _decrypt_response(encrypted_response)
    
    return {
        "success": True,
        "data": decrypted_data,
        "raw_encrypted_response": encrypted_response
    }


def _map_grievance_type(farmer_description: str) -> Dict[str, Any] | None:
    """
    Internal function to map farmer's grievance description to the most appropriate grievance type
    
    Args:
        farmer_description: Farmer's description of their grievance
    
    Returns:
        Dictionary containing the matched grievance type and confirmation message
        Returns None if no suitable match is found
    """
    logger.info(f"Mapping grievance type for description: {farmer_description}")
    farmer_desc_lower = farmer_description.lower()
    
    # Score each grievance type based on keyword matches
    scores = {}
    for code, keywords in GRIEVANCE_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in farmer_desc_lower:
                score += 1
        scores[code] = score
    
    logger.info(f"Grievance type scores: {scores}")
    best_match = max(scores, key=scores.get)
    best_score = scores[best_match]
    
    logger.info(f"Best match: {best_match} with score: {best_score}")
    
    if best_score == 0:
        logger.info("No suitable grievance type match found (score is 0)")
        return None
    
    logger.info(f"Matched grievance: {best_match} - {GRIEVANCE_TYPES[best_match]}")
    return {
        "grievance_code": best_match,
        "grievance_description": GRIEVANCE_TYPES[best_match],
    }


def create_grievance(
    identity_no: str,
    grievance_description: str,
) -> str:
    """
    Create and submit a grievance to the PM-KISAN portal
    
    Args:
        identity_no: Identity number (e.g., "PM-KISAN registration number" or Aadhaar number)
        grievance_description: Description of the grievance
    
    Returns:
        Response about the grievance submission status
    """
    
    mapping_result = _map_grievance_type(grievance_description)
    
    if mapping_result is None:
        return "Please provide more specific details about your grievance so we can help you better."
    
    grievance_type = mapping_result["grievance_code"]
    
    result = submit_grievance(
        identity_no,
        grievance_type,
        grievance_description
    )
    
    if result["success"]:
        message = result['data'].get('message', 'Grievance submitted successfully')
        return message
    else:
        logger.error(f"Failed to submit grievance: {result['error']}")
        return f"Failed to submit grievance: {result['error']}"


def check_grievance_registration_status(
    identity_no: str,
    api_url: str = f"{base_url}/GrievanceCheck"
) -> Dict[str, Any]:
    """
    Check the grievance status using registration number or Aadhaar number
    
    Args:
        identity_no: Registration number or Aadhaar number (12-digit)
        api_url: API endpoint URL for grievance check
    
    Returns:
        Dictionary containing the response from the grievance check service
    """
    actual_identity_no, request_type, error_dict = _handle_identity_number(identity_no)
    if error_dict:
        return error_dict
    
    request_data = {
        "Type": request_type,
        "TokenNo": "PMK_123456",
        "IdentityNo": actual_identity_no
    }
    
    response = _encrypt_and_send(request_data, api_url)
    
    if response.status_code != 200:
        logger.error(f"Grievance registration status check failed with status {response.status_code}: {response.text}")
        return {
            "success": False,
            "error": f"HTTP {response.status_code}: {response.text}",
            "status_code": response.status_code
        }
    
    response_data = response.json()
    
    if "d" in response_data and "output" in response_data["d"]:
        encrypted_response = response_data["d"]["output"]
    elif "output" in response_data:
        encrypted_response = response_data["output"]
    else:
        logger.error("Invalid response format from grievance registration status service")
        return {
            "success": False,
            "error": "Invalid response format from grievance check service",
            "raw_response": response_data
        }
    
    decrypted_data = _decrypt_response(encrypted_response)
    
    return {
        "success": True,
        "data": decrypted_data,
        "raw_encrypted_response": encrypted_response
    }


def check_grievance_status(
    identity_no: str,
    api_url: str = f"{base_url}/GrievanceStatusCheck"
) -> Dict[str, Any]:
    """
    Check the grievance status using registration number or Aadhaar number
    
    Args:
        identity_no: Registration number or Aadhaar number (12-digit)
        api_url: API endpoint URL for grievance status check
    
    Returns:
        Dictionary containing the response from the grievance check service
    """
    actual_identity_no, request_type, error_dict = _handle_identity_number(identity_no)
    if error_dict:
        return error_dict
    
    if request_type == "IdentityNo":
        request_type = "IdentityNo_Status"
    elif request_type == "Reg_No":
        request_type = "Reg_No_Status"
    
    request_data = {
        "Type": request_type,
        "TokenNo": "PMK_123456",
        "IdentityNo": actual_identity_no
    }
    
    response = _encrypt_and_send(request_data, api_url)
    
    if response.status_code != 200:
        logger.error(f"Grievance status check failed with status {response.status_code}: {response.text}")
        return {
            "success": False,
            "error": f"HTTP {response.status_code}: {response.text}",
            "status_code": response.status_code
        }
    
    response_data = response.json()
    
    if "d" in response_data and "output" in response_data["d"]:
        encrypted_response = response_data["d"]["output"]
    elif "output" in response_data:
        encrypted_response = response_data["output"]
    else:
        logger.error("Invalid response format from grievance status service")
        return {
            "success": False,
            "error": "Invalid response format from grievance check service",
            "raw_response": response_data
        }
    
    decrypted_data = _decrypt_response(encrypted_response)
    if decrypted_data.get("Responce") == "True" and "details" in decrypted_data and decrypted_data["details"]:
        details = decrypted_data["details"][0]  
        formatted_response = []
        
        if details.get("Reg_No"):
            formatted_response.append(f"Registration Number: {details['Reg_No']}")
        
        formatted_response.append(f"\nGrievance Details:")
        grievance_date = details.get("GrievanceDate", "")
        if grievance_date:
            formatted_response.append(f"Date of Grievance: {grievance_date}")
        
        if details.get("GrievanceDescription"):
            formatted_response.append(f"\nGrievance Description: {details['GrievanceDescription']}")
        
        formatted_response.append(f"\nOfficer Response Details:")
        
        grievance_status = details.get("GrievanceStatus")
        if grievance_status:
            formatted_response.append(f"Status: {grievance_status}")
        else:
            formatted_response.append("Status: Pending")
        
        officer_reply = details.get("OfficerReply")
        if officer_reply:
            formatted_response.append(f"Officer Reply: {officer_reply}")
            
            office_reply_date = details.get("OfficeReplyDate")
            if office_reply_date:
                formatted_response.append(f"Reply Date: {office_reply_date}")
        else:
            formatted_response.append("Officer Reply: Not yet responded")
        
        response_text = "\n".join(formatted_response)
        
        return {
            "success": True,
            "data": response_text,
        }
    else:
        error_message = decrypted_data.get("message") or "No grievances found for this registration number."
        return {
            "success": False,
            "error": error_message,
            "data": decrypted_data
        }

