import {register} from 'node:module';
register('./browser-import-loader.mjs',import.meta.url);
register('./sample-sdk-import-loader.mjs',import.meta.url);
