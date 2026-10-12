import SwaggerUIBundle from 'swagger-ui-dist/swagger-ui-bundle.js';
import 'swagger-ui-dist/swagger-ui.css';
import './docs.css';
import { agentDocsOptions } from './options';

SwaggerUIBundle({ ...agentDocsOptions, dom_id: '#swagger-ui' });
