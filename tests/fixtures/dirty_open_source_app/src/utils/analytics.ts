import posthog from 'posthog-js';
import * as Sentry from '@sentry/react';
export function trackEvent(name: string) { posthog.capture(name); }
export function reportError(error: Error) { Sentry.captureException(error); }
