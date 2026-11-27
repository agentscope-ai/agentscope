import DOMPurify from 'dompurify';

/** A static document with no scripts, navigation, forms or network access. */
export function staticHtml(content: string): string {
	const clean = DOMPurify.sanitize(content, {
		WHOLE_DOCUMENT: true,
		FORBID_TAGS: [
			'script',
			'iframe',
			'object',
			'embed',
			'base',
			'meta',
			'form',
			'input',
			'button',
			'textarea',
			'select',
		],
		FORBID_ATTR: ['href', 'target', 'download', 'contenteditable'],
	});
	const document = new DOMParser().parseFromString(clean, 'text/html');
	const policy = document.createElement('meta');
	policy.httpEquiv = 'Content-Security-Policy';
	policy.content =
		"default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'";
	document.head.prepend(policy);
	return `<!doctype html>\n${document.documentElement.outerHTML}`;
}
