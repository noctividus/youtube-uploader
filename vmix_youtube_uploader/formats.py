"""Extension choices for discovery, not a guarantee of decodable video content."""

# YouTube's published list mixes containers and codecs:
# https://support.google.com/youtube/troubleshooter/2888402?hl=en
# Include its extension spellings plus common MPEG/3GPP suffixes. ProRes,
# DNxHR, CineForm, and HEVC are codecs, not additional extension checkboxes;
# they are detected through their container (for example MOV, AVI, or MP4).
YOUTUBE_EXTENSIONS = (
    ".mp4", ".mov", ".avi", ".wmv", ".webm", ".flv",
    ".mpg", ".mpeg", ".mpeg-1", ".mpeg-2", ".mpeg4", ".mpegps",
    ".m1v", ".m2v", ".3gp", ".3gpp",
)
# Preserve the original recording filter, including MKV, which is not named
# on that YouTube help page. Label it as an additional choice in Settings.
EXTENSION_CHOICES = (*YOUTUBE_EXTENSIONS, ".mkv")
DEFAULT_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi")
