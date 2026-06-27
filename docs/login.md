# Suno login on iPhone

Only needed if you have no computer (the [README](../README.md#getting-your-session-token)
steps are quicker). iOS hides the `__client` cookie, so capture it from network traffic:

1. Install [Stream](https://apps.apple.com/app/id1312141691), allow its VPN, install its
   certificate, then trust it at **Settings → General → About → Certificate Trust Settings**.
2. Start capturing, sign in at [suno.com](https://suno.com) in Safari, and open your Library.
3. Open a request to `auth.suno.com`, copy the `Cookie` header, and paste it into the
   integration (it keeps only `__client`).
4. Remove Stream's profile when done.
