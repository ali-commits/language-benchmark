<?php

return ['name' => 'Language benchmark', 'env' => 'production', 'debug' => false, 'url' => 'http://localhost', 'timezone' => 'UTC', 'locale' => 'en', 'fallback_locale' => 'en', 'key' => getenv('APP_KEY') ?: null, 'cipher' => 'AES-256-CBC'];
