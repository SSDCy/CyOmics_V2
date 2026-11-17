// Import-only stand-in. Tests must explicitly inject the fixture createClient seam.
// This is not the real SDK and no actual SDK authentication path is tested.
export class AuthClient {constructor(){throw Error('REAL_SDK_EXECUTION_FORBIDDEN_EXPLICIT_FIXTURE_CLIENT_REQUIRED');}}
