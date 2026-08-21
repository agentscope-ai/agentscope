import { describe, expect, it } from 'vitest';

import { withCredentialMode } from './channel-form-state';

describe('withCredentialMode', () => {
	it('switches from QR to manual and clears the stale binding atomically', () => {
		const result = withCredentialMode(
			{
				credentialMode: 'qr_code' as const,
				credentialBindingId: 'binding-1',
				name: 'Feishu',
			},
			'manual',
		);

		expect(result).toEqual({
			credentialMode: 'manual',
			credentialBindingId: null,
			name: 'Feishu',
		});
	});
});
