pub fn accepts_body(path: &str, method: &str, bytes: usize) -> bool {
    // Base64 expands the 16 MiB attachment limit; other owner requests stay small.
    let limit = if path == "/api/attachments" && method == "POST" { 24_000_000 } else { 2_000_000 };
    bytes <= limit
}

#[cfg(test)]
mod tests {
    use super::accepts_body;

    #[test]
    fn full_audio_and_image_uploads_fit_with_base64_framing() {
        let encoded_audio = (16 * 1024 * 1024 + 2) / 3 * 4 + 1_000;
        assert!(accepts_body("/api/attachments", "POST", encoded_audio));
        assert!(accepts_body("/api/attachments", "POST", 6_000_000));
    }

    #[test]
    fn larger_allowance_is_scoped_to_attachment_creation() {
        for (path, method) in [("/api/profiles", "POST"), ("/api/attachments", "PUT"), ("/api/attachments/example/ocr", "POST")] {
            assert!(!accepts_body(path, method, 2_000_001));
        }
    }

    #[test]
    fn bounded_requests() {
        assert!(accepts_body("/api/attachments", "POST", 24_000_000));
        assert!(!accepts_body("/api/attachments", "POST", 24_000_001));
        assert!(accepts_body("/api/runs", "POST", 2_000_000));
        assert!(!accepts_body("/api/runs", "POST", 2_000_001));
    }
}
