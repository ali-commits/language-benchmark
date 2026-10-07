<?php

use App\Http\Middleware\ConnectionScope;
use App\Support\Database;
use App\Support\InputError;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Foundation\Http\Middleware\ConvertEmptyStringsToNull;
use Illuminate\Foundation\Http\Middleware\TrimStrings;
use Symfony\Component\HttpKernel\Exception\MethodNotAllowedHttpException;
use Symfony\Component\HttpKernel\Exception\NotFoundHttpException;

$app = Application::configure(basePath: dirname(__DIR__))
    ->withRouting(api: __DIR__.'/../routes/api.php', apiPrefix: '')
    ->withMiddleware(function (Middleware $m) {
        $m->remove([TrimStrings::class, ConvertEmptyStringsToNull::class]);
        $m->append(ConnectionScope::class);
    })
    ->withExceptions(function (Exceptions $e) {
        $e->shouldRenderJsonWhen(fn () => true);
        $e->render(fn (InputError $x) => response()->json(['detail' => $x->detail], 422));
        $e->render(fn (NotFoundHttpException $x) => response()->json(['detail' => 'Not Found'], 404));
        $e->render(fn (MethodNotAllowedHttpException $x) => response()->json(['detail' => 'Method Not Allowed'], 405, $x->getHeaders()));
    })
    ->create();

$app->singleton(Database::class);

return $app;
