from app.routers.chat_query_utils import extract_image_id, normalize_chat_query


def test_extract_image_id_from_embedded_uuid_text():
    image_id = "9b5c8815-772d-46ec-88bf-bbfbd8e6a96e"

    assert extract_image_id(f"please analyze this image {image_id}") == image_id


def test_normalize_chat_query_embedded_uuid_to_image_url():
    image_id = "9b5c8815-772d-46ec-88bf-bbfbd8e6a96e"

    query, is_image_analysis = normalize_chat_query(f"please analyze this image {image_id}")

    assert is_image_analysis is True
    assert f"/api/image/{image_id}" in query
    assert "[IMAGE_URL:" in query
