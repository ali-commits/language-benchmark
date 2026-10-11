package benchmark;

import com.zaxxer.hikari.*;
import java.sql.*;
import java.util.concurrent.atomic.*;
import javax.sql.DataSource;
import org.springframework.boot.*;
import org.springframework.boot.autoconfigure.*;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DelegatingDataSource;
import org.springframework.transaction.support.TransactionTemplate;

@SpringBootApplication
public class Application {
  static final LongAdder ACQUIRES = new LongAdder();
  static final LongAdder NANOS = new LongAdder();

  public static void main(String[] args) {
    var context = SpringApplication.run(Application.class, args);
    try {
      var source = (DelegatingDataSource) context.getBean(DataSource.class);
      var pool = (HikariDataSource) source.getTargetDataSource();
      Tuning.writeReceipt(System.getenv("RUN_DIR"), System.getenv("RUN_ID"), Protocol.HASH_CONCURRENCY, pool.getMaximumPoolSize(),pool.getMinimumIdle());
    } catch (Exception e) {
      context.close();
      throw new IllegalStateException("unable to publish runtime tuning receipt", e);
    }
  }

  @Bean
  org.springframework.beans.factory.DisposableBean hashExecutorLifecycle() { return () -> {if(Protocol.HASH_EXECUTOR!=null) Protocol.HASH_EXECUTOR.close();}; }

  @Bean
  Protocol protocol() {
    return new Protocol(
        System.getenv("JWT_SECRET"),
        Integer.parseInt(System.getenv().getOrDefault("JWT_TTL_SECONDS", "3600")));
  }

  @Bean
  DataSource dataSource() throws Exception {
    var parsed = DatabaseConfig.parse(System.getenv("DATABASE_URL"), System.getenv("MODE"));
    var cfg = new HikariConfig();
    cfg.setJdbcUrl(parsed.jdbcUrl());
    cfg.setUsername(parsed.username());
    cfg.setPassword(parsed.password());
    var bounds=Tuning.poolBounds(System.getenv("DB_POOL_MAX_SIZE"),System.getenv("DB_POOL_MIN_SIZE"));
    cfg.setMaximumPoolSize(bounds[0]);
    cfg.setMinimumIdle(bounds[1]);
    cfg.setConnectionTimeout(5000);
    cfg.setPoolName("social-api");
    var hikari = new HikariDataSource(cfg);
    return new DelegatingDataSource(hikari) {
      @Override
      public Connection getConnection() throws SQLException {
        long start = System.nanoTime();
        var c = super.getConnection();
        ACQUIRES.increment();
        NANOS.add(System.nanoTime() - start);
        return c;
      }
    };
  }

  @Bean
  NamedParameterJdbcTemplate jdbc(DataSource db) {
    return new NamedParameterJdbcTemplate(db);
  }

  @Bean
  TransactionTemplate transaction(DataSource db) {
    return new TransactionTemplate(new DataSourceTransactionManager(db));
  }
}
