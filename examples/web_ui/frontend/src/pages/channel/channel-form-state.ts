export type CredentialMode = 'manual' | 'qr_code';

/** Apply a credential-tab change as one immutable state transition. */
export function withCredentialMode<
	T extends {
		credentialMode: CredentialMode;
		credentialBindingId: string | null;
	},
>(value: T, credentialMode: CredentialMode): T {
	return {
		...value,
		credentialMode,
		credentialBindingId: credentialMode === 'manual' ? null : value.credentialBindingId,
	};
}
