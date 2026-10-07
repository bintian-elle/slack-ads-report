import requests
import re
import json
from urllib.parse import urlparse


def extract_video_id(url):
    """从 TikTok URL 提取 video_id"""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.tiktok.com', 'tiktok.com'):
        raise ValueError('Expected an HTTPS TikTok video link')
    match = re.fullmatch(r"/@[^/]+/video/(\d+)/?", parsed.path)

    if not match:
        raise ValueError(f"无法从 URL 提取 video_id: {url}")

    return match.group(1)


def find_video_object(obj, video_id):
    """
    递归搜索 JSON，找到 id == video_id 且包含 stats 的视频对象
    """
    if isinstance(obj, dict):

        # TikTok 视频对象通常有 id + stats
        if str(obj.get("id")) == str(video_id):
            stats = obj.get("stats")

            if isinstance(stats, dict):
                return obj

        for value in obj.values():
            result = find_video_object(value, video_id)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:
            result = find_video_object(item, video_id)

            if result:
                return result

    return None


def extract_json_scripts(html):
    """
    提取页面中所有可能包含 TikTok 数据的 JSON script
    """
    pattern = r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>'

    matches = re.findall(
        pattern,
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    results = []

    for raw_json in matches:
        try:
            data = json.loads(raw_json)
            results.append(data)
        except json.JSONDecodeError:
            continue

    return results


class TikTokBlocked(RuntimeError):
    def __init__(self, status, retry_after=None):
        super().__init__('TikTok HTTP %s; batch stopped' % status)
        self.status = status
        self.retry_after = retry_after


def get_tiktok_metrics(url):

    video_id = extract_video_id(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    response = requests.get(
        'https://www.tiktok.com' + urlparse(url).path.rstrip('/'),
        headers=headers,
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code in (403, 429):
        raise TikTokBlocked(response.status_code, response.headers.get('Retry-After'))
    if 300 <= response.status_code < 400:
        raise RuntimeError('Unexpected TikTok redirect; no follow-up request')
    response.raise_for_status()

    print("STATUS:", response.status_code)
    print("VIDEO ID:", video_id)
    print("HTML LENGTH:", len(response.text))

    json_objects = extract_json_scripts(response.text)

    print("JSON SCRIPT COUNT:", len(json_objects))

    # 精确寻找目标 video_id
    for data in json_objects:

        video = find_video_object(data, video_id)

        if video:

            stats = video.get("stats", {})

            print("\nFOUND TARGET VIDEO")
            print("ID:", video.get("id"))

            result = {
                "video_id": video_id,
                "views": stats.get("playCount"),
                "likes": stats.get("diggCount"),
                "comments": stats.get("commentCount"),
                "saves": stats.get("collectCount"),
                "shares": stats.get("shareCount"),
            }

            return result

    raise RuntimeError(
        f"找不到 video_id={video_id} 对应的 TikTok JSON 数据"
    )


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('url', nargs='?', default='https://www.tiktok.com/@jessmollybell/video/7616414148779363606')
    print(json.dumps(get_tiktok_metrics(parser.parse_args().url)))
