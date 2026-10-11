<?php

namespace App\Http\Middleware;

use App\Support\Database;
use Closure;
use Illuminate\Http\Request;

final class ConnectionScope
{
    public function handle(Request $request, Closure $next)
    {
        try {
            return $next($request);
        } finally {
            app(Database::class)->release();
        }
    }
}
