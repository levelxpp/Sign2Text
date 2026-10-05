# Privacy Policy for Sign2Text

**Last Updated:** April 16, 2026

This Privacy Policy describes how Sign2Text ("we," "our," or "the Application") handles information in connection with your use of our services. Sign2Text is a real-time translation tool featuring ASL, Voice, and Letter translation modes.

---

## 1. Information We Process

### 1.1 Real-Time Media Data
To provide translation services, the Application processes camera and microphone data:
*   **Camera Frames (ASL & Letter Modes):** When you enable your camera, individual frames are transmitted to the backend for real-time analysis by AI models. We process these frames in-memory.
*   **Microphone Audio (Voice Mode):** When you enable your microphone, audio streams are transmitted to the backend and then to third-party providers for Speech-to-Text conversion.
*   **Retention:** In the current implementation, based on a review of the source code, raw camera video, individual frames, and audio recordings are processed in-memory and are not persistently stored by the Application on its backend after the active session is complete.

### 1.2 User-Provided Information
*   **Contributor Program:** If you voluntarily submit your information via our "Contribute" form, we collect your **email address**. This information is used to manage project outreach and program updates. 
*   **Public Feedback:** If you voluntarily submit public feedback through the landing page, we may collect the optional **name** and the submitted **message**.
*   **Administrative Access:** For authorized administrators, the Application may log basic data regarding administrative actions (e.g., exports, lead updates, or deletions) for security and auditing purposes.

### 1.3 System & Storage Data
The Application uses browser-based storage and session handling to improve your experience:
*   **Browser LocalStorage:** Translation history and certain local preferences or settings (such as language selection and caption size) may be stored in browser local storage. This data remains on your device until you clear your browser cache or history.
*   **Authenticated Sessions:** If you use features requiring authentication across ASL, Voice, or Letter modes, cookie-backed sessions may be used to maintain your authenticated access. These sessions may store basic Google-authenticated profile identifiers provided by the authentication provider, such as subject ID, email, display name, avatar URL, and email-verification state.
*   **Optional Backend Transcript Storage:** When you are authenticated and backend transcript storage is enabled, the Application may sync or merge saved transcript history to backend persistence associated with your Google-authenticated account. If backend transcript storage is unavailable, local browser history remains available on your device.

---

## 2. Third-Party Services
Sign2Text integrates with several third-party providers to perform advanced AI and authentication tasks. Your data is handled according to their respective policies when these features are active:
*   **Google Cloud:** Used for user authentication (OAuth), Speech-to-Text, and Text-to-Speech services.
*   **Microsoft Azure:** Used for text translation services.
*   **Ollama:** Used specifically for ASL gloss-to-English normalization. Depending on the project configuration, this may run locally on the server or via a remote endpoint.

*Note: The availability and behavior of these integrations depend on the specific deployment and environment configuration. Some integrations and data flows depend on deployment configuration and enabled services.*

---

## 3. Data Storage and Security
*   **Local Infrastructure:** By default, contributor leads and public feedback are stored in a local database (SQLite) on the configured backend for the active deployment.
*   **Optional Supabase Storage:** When Supabase is enabled, backend services may store Google-authenticated user profiles, transcript history, contributor leads, and feedback in Supabase. These integrations are backend-only; Supabase credentials are not exposed to the browser.
*   **Security Measures:** We implement standard security practices designed to protect your information, including restricted administrative access. However, no method of transmission or electronic storage is 100% secure.
*   **Deployment Variability**: Because Sign2Text is a flexible project that can be deployed in various environments (local, hosted, or proxied), the specific data handling and security properties may vary based on how the Application is configured.

---

## 4. Your Choices
*   **Local History:** You can clear your translation history or preferences at any time by clearing your browser's LocalStorage or using built-in "Clear History" features where available.
*   **Permissions:** You can revoke camera and microphone permissions through your browser settings at any time.

---

## 5. Changes to This Privacy Policy
We reserve the right to modify this Privacy Policy at any time. Changes will be effective upon posting the updated Privacy Policy. Your continued use of the Application after such changes constitutes your acceptance of the updated Privacy Policy.

---

## 6. Contact Us
If you have questions about this Privacy Policy, please contact Koko Jamgotchian at:
[TODO: Project owner to provide contact email/address]
